"""A5 — Gradient and calibration assessment.

Fits a low-order polynomial surface and a radial vignetting model to the A1
background map, and computes per-channel background levels for color-balance
checks. These are the calibration / flat-fielding signature metrics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import optimize

from .image_chars import SEG_BACKGROUND, SEG_TARGET, ImageCharacterization
from .logging import get_logger

log = get_logger("apodornot.gradient")


# ---------------------------------------------------------------------------- #
# Data model
# ---------------------------------------------------------------------------- #


@dataclass
class GradientResult:
    poly_order: int
    poly_coefficients: np.ndarray  # flattened — length depends on order
    peak_to_peak: float            # max-min of fitted surface
    noise_level: float             # median RMS used for normalization
    gradient_ratio: float          # peak_to_peak / noise_level (3x ~ noticeable, 10x ~ bad)
    direction_deg: float           # 0 = +x, 90 = +y; angle of steepest slope


@dataclass
class VignettingResult:
    fit_succeeded: bool
    center_to_corner_falloff: float  # fraction (0.05 = 5% darker in corners)
    radial_amplitude: float
    radial_power: float


@dataclass
class ColorBalanceResult:
    bg_means: dict[str, float]
    deviation_vector: tuple[float, float, float]   # signed deviations (R, G, B)
    deviation_magnitude: float


@dataclass
class CalibrationAssessment:
    gradient: GradientResult
    vignetting: VignettingResult
    color_balance: ColorBalanceResult | None

    def summary(self) -> dict:
        return {
            "gradient_peak_to_peak": self.gradient.peak_to_peak,
            "gradient_ratio": self.gradient.gradient_ratio,
            "gradient_direction_deg": self.gradient.direction_deg,
            "vignetting_falloff": self.vignetting.center_to_corner_falloff,
            "color_balance_magnitude": (
                self.color_balance.deviation_magnitude if self.color_balance else None
            ),
        }


# ---------------------------------------------------------------------------- #
# A5.1 — Gradient measurement
# ---------------------------------------------------------------------------- #


def _polyfit_surface(z: np.ndarray, order: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Fit a polynomial surface of given order. Returns (coeffs, fitted).

    For large inputs we stride-downsample first. A 24 MP background map
    otherwise allocates ~1.2 GB just for the (H·W, 6) design matrix at order=2,
    plus another ~GB of lstsq SVD workspace — enough to OOM a 4 GB pipeline
    machine. The fit is determined by 6 coefficients; a 128-px-per-side grid
    (≥16K samples) is comfortably overdetermined and produces a surface
    numerically indistinguishable from the full-resolution one on the smooth
    SEP background.
    """
    h, w = z.shape
    step = max(1, min(h, w) // 128)
    if step > 1:
        z = z[::step, ::step]
        h, w = z.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    xn = (xx - (w - 1) / 2.0) / max(w / 2.0, 1.0)
    yn = (yy - (h - 1) / 2.0) / max(h / 2.0, 1.0)

    cols = []
    powers = []
    for i in range(order + 1):
        for j in range(order + 1 - i):
            cols.append((xn**i) * (yn**j))
            powers.append((i, j))
    A = np.stack([c.ravel() for c in cols], axis=1)
    b = z.ravel()
    coeffs, *_ = np.linalg.lstsq(A, b, rcond=None)
    fitted = (A @ coeffs).reshape(h, w)
    return np.asarray(coeffs), fitted


def measure_gradient(
    background: np.ndarray, rms: np.ndarray, *, order: int = 2
) -> GradientResult:
    """Fit a low-order surface to the SEP background map and quantify gradient."""
    coeffs, fitted = _polyfit_surface(background.astype(np.float64), order=order)
    p2p = float(fitted.max() - fitted.min())
    noise_level = float(np.median(rms))
    ratio = p2p / max(noise_level, 1e-9)

    # Direction of steepest descent: numerical gradient of fitted surface.
    gy, gx = np.gradient(fitted)
    direction = float(np.degrees(np.arctan2(float(gy.mean()), float(gx.mean()))) % 360.0)

    return GradientResult(
        poly_order=order,
        poly_coefficients=coeffs,
        peak_to_peak=p2p,
        noise_level=noise_level,
        gradient_ratio=ratio,
        direction_deg=direction,
    )


# ---------------------------------------------------------------------------- #
# A5.2 — Vignetting detection
# ---------------------------------------------------------------------------- #


def _radial_falloff(r: np.ndarray, center: float, amp: float, power: float) -> np.ndarray:
    return center - amp * np.power(np.maximum(r, 0.0), power)


def detect_vignetting(
    background: np.ndarray, segmentation: np.ndarray | None = None
) -> VignettingResult:
    """Fit center - amp * r^power; measure center-to-corner percentage falloff.

    Robust two-stage fit:

      1. Restrict to ``SEG_BACKGROUND`` pixels (excludes stars + segmented target).
      2. Bin those by radius into 16 shells, take the **10th percentile** of
         pixel values per shell, and fit the radial-falloff model to the
         (shell_radius, p10) pairs.

    Step 2 is the key. SEP's mesh-based background still leaks bright central
    nebula brightness into the inner-shell pixels (the meshes near the cavity
    interpolate over the diffuse Hα floor). The shell-p10 picks the *true sky*
    pixels in each shell — the bottom decile of background-classified
    intensities — so a nebula-centered image isn't misread as having a bright
    center. Without this, Tiffany's Rosette reported 78% spurious vignetting
    (design doc A5.2).
    """
    h, w = background.shape
    step = max(1, min(h, w) // 128)
    z = background[::step, ::step].astype(np.float64)
    yy, xx = np.mgrid[0 : z.shape[0], 0 : z.shape[1]]
    cy = (z.shape[0] - 1) / 2.0
    cx = (z.shape[1] - 1) / 2.0
    rmax = max(np.hypot(cx, cy), 1e-9)
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rmax  # in [0, ~1]

    z_flat = z.ravel()
    r_flat = r.ravel()

    if segmentation is not None:
        seg_ds = segmentation[::step, ::step]
        mask_flat = (seg_ds.ravel() == SEG_BACKGROUND)
        kept = int(mask_flat.sum())
        if kept >= 200:
            z_flat = z_flat[mask_flat]
            r_flat = r_flat[mask_flat]
        else:
            log.warning(
                "vignetting fit: only %d background pixels after masking, "
                "falling back to unmasked fit (results may be biased by target)",
                kept,
            )

    if z_flat.size < 10:
        return VignettingResult(
            fit_succeeded=False,
            center_to_corner_falloff=float("nan"),
            radial_amplitude=float("nan"),
            radial_power=float("nan"),
        )

    # ---- Robust shell-p10 fit ---------------------------------------------- #
    # For nebula-centered images, the inner shells have NO true sky pixels —
    # the entire inner field is target. Detect this by comparing each shell's
    # p10 against the median of the outer shells; shells more than 1.5x the
    # outer median are excluded as "no sky here, only target leakage". This
    # makes the fit measure genuine optical vignetting in the regions where
    # sky exists, instead of misreading target brightness as falloff.
    n_shells = 16
    shell_edges = np.linspace(0.0, 1.0, n_shells + 1)
    shell_r_all: list[float] = []
    shell_v_all: list[float] = []
    for i in range(n_shells):
        in_shell = (r_flat >= shell_edges[i]) & (r_flat < shell_edges[i + 1])
        if int(in_shell.sum()) < 20:
            continue
        shell_r_all.append((shell_edges[i] + shell_edges[i + 1]) / 2.0)
        shell_v_all.append(float(np.percentile(z_flat[in_shell], 10)))

    if len(shell_r_all) < 4:
        log.warning("vignetting fit: only %d shells with enough pixels", len(shell_r_all))
        return VignettingResult(
            fit_succeeded=False,
            center_to_corner_falloff=float("nan"),
            radial_amplitude=float("nan"),
            radial_power=float("nan"),
        )

    # Outer-shell baseline: median of shells with r >= 0.5.
    outer = [(rr, vv) for rr, vv in zip(shell_r_all, shell_v_all) if rr >= 0.5]
    if len(outer) >= 3:
        outer_median = float(np.median([vv for _, vv in outer]))
        # Drop inner shells whose p10 exceeds 1.5x the outer baseline — those
        # are dominated by target leakage, not sky.
        kept = [
            (rr, vv) for rr, vv in zip(shell_r_all, shell_v_all)
            if rr >= 0.5 or vv <= 1.5 * outer_median
        ]
        if len(kept) >= 4:
            shell_r = [rr for rr, _ in kept]
            shell_v = [vv for _, vv in kept]
        else:
            # Too few clean inner shells; fit on outer-only.
            shell_r = [rr for rr, _ in outer]
            shell_v = [vv for _, vv in outer]
    else:
        shell_r = shell_r_all
        shell_v = shell_v_all

    sr = np.asarray(shell_r)
    sv = np.asarray(shell_v)
    p0 = [float(sv[0]), max(float(sv[0] - sv[-1]), 0.001), 2.0]

    try:
        popt, _ = optimize.curve_fit(
            _radial_falloff,
            sr,
            sv,
            p0=p0,
            bounds=([-np.inf, 0.0, 0.5], [np.inf, np.inf, 6.0]),
            maxfev=400,
        )
        center, amp, power = popt
        center_val = float(center)
        corner_val = float(_radial_falloff(np.array([1.0]), center, amp, power)[0])
        if abs(center_val) < 1e-9:
            falloff = 0.0
        else:
            falloff = float((center_val - corner_val) / abs(center_val))
        # Vignetting can only DARKEN the corners; a positive amp with corner < center
        # means falloff is positive. If the fit produces a negative falloff (rare,
        # would mean corners brighter than center), clamp to 0 — that's not vignetting.
        falloff = max(0.0, falloff)
        return VignettingResult(
            fit_succeeded=True,
            center_to_corner_falloff=falloff,
            radial_amplitude=float(amp),
            radial_power=float(power),
        )
    except (RuntimeError, ValueError):
        return VignettingResult(
            fit_succeeded=False,
            center_to_corner_falloff=float("nan"),
            radial_amplitude=float("nan"),
            radial_power=float("nan"),
        )


# ---------------------------------------------------------------------------- #
# A5.3 — Per-channel background color balance
# ---------------------------------------------------------------------------- #


def measure_color_balance(
    color: np.ndarray, segmentation: np.ndarray
) -> ColorBalanceResult | None:
    """Per-channel background level + deviation from neutral gray."""
    if color is None:
        return None
    bg_mask = segmentation == SEG_BACKGROUND
    if not np.any(bg_mask):
        return None

    means = {}
    for i, name in enumerate(("R", "G", "B")):
        means[name] = float(np.median(color[..., i][bg_mask]))

    avg = (means["R"] + means["G"] + means["B"]) / 3.0
    if avg < 1e-9:
        return ColorBalanceResult(bg_means=means, deviation_vector=(0.0, 0.0, 0.0), deviation_magnitude=0.0)

    dev = (means["R"] - avg, means["G"] - avg, means["B"] - avg)
    # Magnitude relative to the average background level — normalized.
    mag = float(np.sqrt(sum(d * d for d in dev)) / avg)
    return ColorBalanceResult(bg_means=means, deviation_vector=dev, deviation_magnitude=mag)


# ---------------------------------------------------------------------------- #
# Top-level A5 pipeline
# ---------------------------------------------------------------------------- #


def assess_calibration(chars: ImageCharacterization) -> CalibrationAssessment:
    grad = measure_gradient(chars.background, chars.rms)
    vig = detect_vignetting(chars.background, chars.segmentation)
    cb = measure_color_balance(chars.color, chars.segmentation)
    log.info(
        "A5: gradient ratio %.2f, vignetting falloff %.3f, color dev %.4f",
        grad.gradient_ratio,
        vig.center_to_corner_falloff,
        cb.deviation_magnitude if cb else float("nan"),
    )
    return CalibrationAssessment(gradient=grad, vignetting=vig, color_balance=cb)


__all__ = [
    "CalibrationAssessment",
    "ColorBalanceResult",
    "GradientResult",
    "VignettingResult",
    "assess_calibration",
    "detect_vignetting",
    "measure_color_balance",
    "measure_gradient",
]
