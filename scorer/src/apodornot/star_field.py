"""A2 — Star field analysis.

For each detected star (or a spatially representative subsample if the catalog
is huge), fits a 2D Moffat profile and extracts FWHM, eccentricity, position
angle, beta wing parameter, and fit quality. Aggregates per-star measurements
into field-level statistics that diagnose focus, tracking, and optical issues.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import curve_fit

from .image_chars import ImageCharacterization, SourceCatalog
from .logging import get_logger

log = get_logger("apodornot.star_field")


# ---------------------------------------------------------------------------- #
# Data model
# ---------------------------------------------------------------------------- #


@dataclass
class StarMeasurement:
    """Per-star fit results."""

    x: float
    y: float
    flux: float
    fwhm: float           # geometric mean FWHM in pixels
    fwhm_x: float
    fwhm_y: float
    eccentricity: float   # sqrt(1 - (b/a)^2), 0 = round, ->1 = elongated
    pa_deg: float         # position angle of major axis, degrees CCW from +x
    beta: float
    chi2_red: float       # reduced chi-squared / fit residual quality
    saturated: bool
    star_color_rgb: tuple[float, float, float] | None = None  # for A6


@dataclass
class QuadrantStats:
    """Per-quadrant aggregates. Quadrants are labeled tl/tr/bl/br, split at
    the image center (origin top-left)."""
    n: int
    median_fwhm: float
    median_eccentricity: float


@dataclass
class FieldVariation:
    median_fwhm: float
    fwhm_std: float
    fwhm_corner_excess: float       # excess FWHM in corners vs center, in fractions
    median_eccentricity: float
    eccentricity_pattern: str       # "uniform_drift" | "radial" | "random_seeing" | "clean"
    pa_concentration: float          # mean resultant length R of position angles in [0,1]
    radial_alignment_score: float    # how well PA tracks radial direction (0-1)
    n_stars_used: int
    # Per-quadrant breakdown — exposes tilt direction + asymmetric coma to
    # the deterministic side. tl=top-left, tr=top-right, bl=bottom-left,
    # br=bottom-right (origin top-left).
    quadrants: dict = field(default_factory=dict)
    fwhm_quadrant_asymmetry: float = 0.0  # max - min of per-quadrant median FWHM, fraction


@dataclass
class StarFieldResult:
    measurements: list[StarMeasurement] = field(default_factory=list)
    field_variation: FieldVariation | None = None
    star_colors: list[tuple[float, float, float]] = field(default_factory=list)

    def summary(self) -> dict:
        fv = self.field_variation
        quadrants = None
        if fv and fv.quadrants:
            quadrants = {
                label: {
                    "n": q.n,
                    "median_fwhm_px": q.median_fwhm,
                    "median_eccentricity": q.median_eccentricity,
                }
                for label, q in fv.quadrants.items()
            }
        return {
            "n_stars": len(self.measurements),
            "median_fwhm_px": fv.median_fwhm if fv else None,
            "fwhm_std_px": fv.fwhm_std if fv else None,
            "fwhm_corner_excess": fv.fwhm_corner_excess if fv else None,
            "median_eccentricity": fv.median_eccentricity if fv else None,
            "eccentricity_pattern": fv.eccentricity_pattern if fv else None,
            "pa_concentration": fv.pa_concentration if fv else None,
            "radial_alignment_score": fv.radial_alignment_score if fv else None,
            "fwhm_quadrant_asymmetry": fv.fwhm_quadrant_asymmetry if fv else None,
            "quadrants": quadrants,
            "n_star_colors": len(self.star_colors),
        }


# ---------------------------------------------------------------------------- #
# Moffat model
# ---------------------------------------------------------------------------- #


def moffat_2d(
    coords: tuple[np.ndarray, np.ndarray],
    amp: float,
    x0: float,
    y0: float,
    alpha: float,
    beta: float,
    floor: float,
    ratio: float,
    theta: float,
) -> np.ndarray:
    """Anisotropic 2D Moffat with a constant floor.

    ``ratio = b/a`` is the axis ratio, ``theta`` rotates the major axis.
    FWHM along major axis = 2*alpha*sqrt(2^(1/beta) - 1).
    """
    x, y = coords
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    # Rotate into the star's frame.
    xr = (x - x0) * cos_t + (y - y0) * sin_t
    yr = -(x - x0) * sin_t + (y - y0) * cos_t
    # Anisotropic distance: yr scaled by 1/ratio so the minor axis is shorter.
    d2 = (xr / alpha) ** 2 + (yr / (alpha * ratio)) ** 2
    return floor + amp * (1.0 + d2) ** (-beta)


def _fwhm_from_moffat(alpha: float, beta: float) -> float:
    if beta <= 0:
        return float("nan")
    return 2.0 * alpha * math.sqrt(max(0.0, 2.0 ** (1.0 / beta) - 1.0))


# ---------------------------------------------------------------------------- #
# A2.1 — Per-star Moffat fitting
# ---------------------------------------------------------------------------- #


def _fit_one_star(
    cutout: np.ndarray,
    cx: float,
    cy: float,
    initial_alpha: float,
) -> StarMeasurement | None:
    """Fit a single star cutout. Returns None if the fit fails."""
    h, w = cutout.shape
    yy, xx = np.mgrid[0:h, 0:w]

    z = cutout.astype(np.float64)
    if not np.all(np.isfinite(z)):
        return None
    floor0 = float(np.median(z))
    amp0 = float(z.max() - floor0)
    if amp0 <= 0:
        return None

    # Saturation check: clipping looks like a plateau at the normalized ceiling.
    # Both conditions: peak hits ~1.0 AND multiple adjacent pixels are at the peak.
    z_peak = float(z.max())
    plateau_count = int(np.sum(z >= z_peak * 0.999))
    saturated = bool(z_peak >= 0.99 and plateau_count > 3)

    p0 = [amp0, w / 2.0, h / 2.0, initial_alpha, 3.0, floor0, 1.0, 0.0]
    bounds = (
        [0, 0, 0, 0.3, 0.5, -np.inf, 0.05, -np.pi],
        [np.inf, w, h, 25.0, 10.0, np.inf, 1.0, np.pi],
    )

    try:
        popt, _ = curve_fit(
            moffat_2d,
            (xx.ravel(), yy.ravel()),
            z.ravel(),
            p0=p0,
            bounds=bounds,
            maxfev=400,
        )
    except (RuntimeError, ValueError):
        return None

    amp, x0, y0, alpha, beta, floor, ratio, theta = popt
    fwhm_a = _fwhm_from_moffat(alpha, beta)
    fwhm_b = _fwhm_from_moffat(alpha * ratio, beta)
    if not np.isfinite(fwhm_a) or fwhm_a <= 0 or fwhm_b <= 0:
        return None
    fwhm_geom = math.sqrt(fwhm_a * fwhm_b)

    # Compute reduced chi-squared from residuals (use noise = robust std of residuals).
    model = moffat_2d((xx.ravel(), yy.ravel()), *popt).reshape(z.shape)
    resid = z - model
    sigma = max(1.4826 * float(np.median(np.abs(resid - np.median(resid)))), 1e-6)
    dof = max(z.size - len(p0), 1)
    chi2_red = float(np.sum((resid / sigma) ** 2) / dof)

    ratio = float(np.clip(ratio, 1e-3, 1.0))
    eccentricity = math.sqrt(max(0.0, 1.0 - ratio * ratio))

    return StarMeasurement(
        x=cx + (x0 - w / 2.0),
        y=cy + (y0 - h / 2.0),
        flux=float(amp),
        fwhm=float(fwhm_geom),
        fwhm_x=float(fwhm_a),
        fwhm_y=float(fwhm_b),
        eccentricity=float(eccentricity),
        pa_deg=float(math.degrees(theta) % 180.0),
        beta=float(beta),
        chi2_red=chi2_red,
        saturated=bool(saturated),
    )


def _select_representative_stars(
    catalog: SourceCatalog,
    image_shape: tuple[int, int],
    *,
    max_stars: int = 200,
    rng_seed: int = 0xA90D,
) -> np.ndarray:
    """Pick up to ``max_stars`` from ``catalog.stars`` while preserving field coverage.

    Splits the image into an ~sqrt(max_stars) x sqrt(max_stars) grid and samples
    one star per cell (the brightest), filling out remaining slots randomly.
    """
    stars = catalog.stars
    if len(stars) <= max_stars:
        return stars

    h, w = image_shape
    cells = max(1, int(math.sqrt(max_stars)))
    cell_w = w / cells
    cell_h = h / cells

    x = stars["x"].astype(np.float32)
    y = stars["y"].astype(np.float32)
    flux = stars["flux"].astype(np.float32)

    cell_ix = np.minimum((x // cell_w).astype(int), cells - 1)
    cell_iy = np.minimum((y // cell_h).astype(int), cells - 1)
    cell_id = cell_iy * cells + cell_ix

    chosen: list[int] = []
    chosen_set: set[int] = set()
    # Pick the brightest unsaturated star per cell.
    for cid in np.unique(cell_id):
        idx = np.where(cell_id == cid)[0]
        # Pick brightest in cell.
        best = idx[np.argmax(flux[idx])]
        chosen.append(int(best))
        chosen_set.add(int(best))

    if len(chosen) >= max_stars:
        return stars[chosen[:max_stars]]

    rng = np.random.default_rng(rng_seed)
    remaining = np.array([i for i in range(len(stars)) if i not in chosen_set])
    extra = rng.choice(remaining, size=max_stars - len(chosen), replace=False)
    chosen.extend(int(i) for i in extra)
    return stars[np.array(chosen)]


def _extract_cutout(
    image: np.ndarray, x: float, y: float, half: int = 10
) -> tuple[np.ndarray, float, float] | None:
    h, w = image.shape
    xi = int(round(x))
    yi = int(round(y))
    x0, x1 = xi - half, xi + half + 1
    y0, y1 = yi - half, yi + half + 1
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None
    return image[y0:y1, x0:x1].copy(), float(xi), float(yi)


def fit_stars(
    image: np.ndarray,
    catalog: SourceCatalog,
    *,
    max_stars: int = 200,
    cutout_half_size: int = 10,
) -> list[StarMeasurement]:
    """Fit a Moffat profile to a representative subset of detected stars."""
    if catalog.stars.shape[0] == 0:
        return []

    selected = _select_representative_stars(catalog, image.shape, max_stars=max_stars)
    median_psf = max(catalog.median_psf_size, 1.0)
    initial_alpha = float(2.0 * median_psf)  # rough alpha guess

    measurements: list[StarMeasurement] = []
    for rec in selected:
        cut = _extract_cutout(image, float(rec["x"]), float(rec["y"]), half=cutout_half_size)
        if cut is None:
            continue
        cutout, cx, cy = cut
        m = _fit_one_star(cutout, cx, cy, initial_alpha=initial_alpha)
        if m is not None:
            measurements.append(m)
    return measurements


# ---------------------------------------------------------------------------- #
# A2.2 — Field variation analysis
# ---------------------------------------------------------------------------- #


def _classify_eccentricity_pattern(
    measurements: list[StarMeasurement], image_shape: tuple[int, int]
) -> tuple[str, float, float]:
    """Return (pattern_label, pa_concentration, radial_alignment_score)."""
    if len(measurements) < 8:
        return "insufficient_data", 0.0, 0.0

    # Use position angles weighted by eccentricity (round stars contribute
    # nothing meaningful to a pattern).
    pa_rad = np.array([math.radians(2 * m.pa_deg) for m in measurements])  # double-angle
    weights = np.array([m.eccentricity for m in measurements])
    weights = np.clip(weights, 0.0, None)
    if float(np.sum(weights)) <= 0:
        return "clean", 0.0, 0.0

    cos_sum = float(np.sum(weights * np.cos(pa_rad)))
    sin_sum = float(np.sum(weights * np.sin(pa_rad)))
    R = math.hypot(cos_sum, sin_sum) / float(np.sum(weights))

    # Radial alignment: angle between elongation and (star_pos - image_center).
    h, w = image_shape
    cx, cy = w / 2.0, h / 2.0
    radial_alignments = []
    for m in measurements:
        dx, dy = m.x - cx, m.y - cy
        r = math.hypot(dx, dy)
        if r < 1e-3 or m.eccentricity < 0.05:
            continue
        radial_pa = math.atan2(dy, dx)
        elongation_pa = math.radians(m.pa_deg)
        # cos(2*delta) maps both 0 (radial) and pi (anti-radial) to 1 — both are radial.
        radial_alignments.append(math.cos(2.0 * (elongation_pa - radial_pa)))
    radial_score = float(np.mean(radial_alignments)) if radial_alignments else 0.0

    median_ecc = float(np.median([m.eccentricity for m in measurements]))

    if median_ecc < 0.15:
        label = "clean"
    elif R > 0.6:
        label = "uniform_drift"
    elif radial_score > 0.4:
        label = "radial"
    else:
        label = "random_seeing"

    return label, R, radial_score


def analyze_field_variation(
    measurements: list[StarMeasurement], image_shape: tuple[int, int]
) -> FieldVariation:
    """Aggregate per-star fits into field statistics."""
    if not measurements:
        return FieldVariation(
            median_fwhm=float("nan"),
            fwhm_std=float("nan"),
            fwhm_corner_excess=float("nan"),
            median_eccentricity=float("nan"),
            eccentricity_pattern="no_stars",
            pa_concentration=0.0,
            radial_alignment_score=0.0,
            n_stars_used=0,
        )

    fwhms = np.array([m.fwhm for m in measurements])
    eccs = np.array([m.eccentricity for m in measurements])
    median_fwhm = float(np.median(fwhms))
    fwhm_std = float(np.std(fwhms))

    # Corner excess: median FWHM in the corner 25% vs the inner 50% of the field.
    h, w = image_shape
    cx, cy = w / 2.0, h / 2.0
    half_diag = 0.5 * math.hypot(w, h)
    inner_radius = 0.4 * half_diag
    corner_radius = 0.7 * half_diag
    inner_fwhms, corner_fwhms = [], []
    for m, f in zip(measurements, fwhms):
        r = math.hypot(m.x - cx, m.y - cy)
        if r <= inner_radius:
            inner_fwhms.append(f)
        elif r >= corner_radius:
            corner_fwhms.append(f)
    if inner_fwhms and corner_fwhms:
        excess = (float(np.median(corner_fwhms)) / float(np.median(inner_fwhms))) - 1.0
    else:
        excess = float("nan")

    pattern, R, radial = _classify_eccentricity_pattern(measurements, image_shape)

    # Per-quadrant breakdown.
    quadrants: dict[str, QuadrantStats] = {}
    quadrant_buckets: dict[str, list[StarMeasurement]] = {"tl": [], "tr": [], "bl": [], "br": []}
    for m in measurements:
        q = _quadrant_for(m.x, m.y, w, h)
        quadrant_buckets[q].append(m)
    for label, bucket in quadrant_buckets.items():
        if bucket:
            quadrants[label] = QuadrantStats(
                n=len(bucket),
                median_fwhm=float(np.median([m.fwhm for m in bucket])),
                median_eccentricity=float(np.median([m.eccentricity for m in bucket])),
            )
        else:
            quadrants[label] = QuadrantStats(n=0, median_fwhm=float("nan"), median_eccentricity=float("nan"))
    quad_fwhms = [q.median_fwhm for q in quadrants.values() if q.n > 0 and math.isfinite(q.median_fwhm)]
    if len(quad_fwhms) >= 2 and median_fwhm > 0:
        asymmetry = float((max(quad_fwhms) - min(quad_fwhms)) / median_fwhm)
    else:
        asymmetry = 0.0

    return FieldVariation(
        median_fwhm=median_fwhm,
        fwhm_std=fwhm_std,
        fwhm_corner_excess=excess,
        median_eccentricity=float(np.median(eccs)),
        eccentricity_pattern=pattern,
        pa_concentration=R,
        radial_alignment_score=radial,
        n_stars_used=len(measurements),
        quadrants=quadrants,
        fwhm_quadrant_asymmetry=asymmetry,
    )


def _quadrant_for(x: float, y: float, w: int, h: int) -> str:
    """Top-left origin: 'tl' = top-left, 'tr' = top-right, etc."""
    cx, cy = w / 2, h / 2
    if y < cy:
        return "tl" if x < cx else "tr"
    return "bl" if x < cx else "br"


# ---------------------------------------------------------------------------- #
# A2.3 — Star color extraction
# ---------------------------------------------------------------------------- #


def extract_star_colors(
    color_image: np.ndarray | None,
    measurements: list[StarMeasurement],
    *,
    aperture_radius: int = 3,
    saturation_threshold: float = 0.95,
) -> list[tuple[float, float, float]]:
    """Compute per-star RGB ratios for unsaturated bright stars.

    Returns a list aligned with ``measurements`` (None entries dropped). Side
    effect: also writes ``star_color_rgb`` onto each ``StarMeasurement`` whose
    aperture is valid.
    """
    if color_image is None:
        return []

    h, w, _ = color_image.shape
    colors: list[tuple[float, float, float]] = []
    for m in measurements:
        if m.saturated:
            continue
        xi = int(round(m.x))
        yi = int(round(m.y))
        x0, x1 = xi - aperture_radius, xi + aperture_radius + 1
        y0, y1 = yi - aperture_radius, yi + aperture_radius + 1
        if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
            continue
        patch = color_image[y0:y1, x0:x1, :3]
        if float(patch[..., 1].max()) >= saturation_threshold:
            continue  # green channel saturated — color lost
        rgb = (
            float(patch[..., 0].mean()),
            float(patch[..., 1].mean()),
            float(patch[..., 2].mean()),
        )
        # Subtract a simple background from the corner pixels of the aperture.
        bg_pixels = np.array(
            [patch[0, 0], patch[0, -1], patch[-1, 0], patch[-1, -1]]
        ).mean(axis=0)
        rgb = (
            max(rgb[0] - float(bg_pixels[0]), 0.0),
            max(rgb[1] - float(bg_pixels[1]), 0.0),
            max(rgb[2] - float(bg_pixels[2]), 0.0),
        )
        total = sum(rgb)
        if total <= 1e-6:
            continue
        m.star_color_rgb = rgb
        colors.append(rgb)
    return colors


# ---------------------------------------------------------------------------- #
# Top-level A2 pipeline
# ---------------------------------------------------------------------------- #


def analyze_star_field(
    chars: ImageCharacterization,
    *,
    max_stars: int = 200,
    cutout_half_size: int = 10,
) -> StarFieldResult:
    measurements = fit_stars(
        chars.background_subtracted,
        chars.sources,
        max_stars=max_stars,
        cutout_half_size=cutout_half_size,
    )
    log.info("A2: fit %d stars", len(measurements))
    fv = analyze_field_variation(measurements, chars.luminance.shape)
    colors = extract_star_colors(chars.color, measurements)
    log.info(
        "A2: median FWHM = %.2f px, ecc pattern = %s, %d star colors",
        fv.median_fwhm,
        fv.eccentricity_pattern,
        len(colors),
    )
    return StarFieldResult(measurements=measurements, field_variation=fv, star_colors=colors)


__all__ = [
    "FieldVariation",
    "StarFieldResult",
    "StarMeasurement",
    "analyze_field_variation",
    "analyze_star_field",
    "extract_star_colors",
    "fit_stars",
    "moffat_2d",
]
