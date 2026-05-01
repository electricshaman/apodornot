"""A4 — Frequency-domain analysis of target structure.

Cuts target regions from the segmentation mask, applies a window, FFTs and
azimuthally averages to a radial power spectrum, fits a power-law slope, and
detects oversharpening / ringing artifacts by looking for excess power at
specific frequencies — particularly above the cutoff implied by the optical
PSF measured in A2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import signal

from .image_chars import SEG_TARGET, ImageCharacterization
from .logging import get_logger
from .noise import _radial_average

log = get_logger("apodornot.target_freq")


@dataclass
class FreqArtifact:
    kind: str  # "oversharpening" | "ringing" | "above_psf_cutoff"
    frequency_cycles_per_px: float
    excess_db: float
    severity: float  # 0..1


@dataclass
class TargetFreqResult:
    radial_power: np.ndarray = field(repr=False)
    radial_freq: np.ndarray = field(repr=False)  # cycles/px (Nyquist = 0.5)
    spectral_slope: float = float("nan")
    spectral_intercept: float = float("nan")
    effective_resolution_cycles_per_px: float = float("nan")  # f at noise-floor knee
    artifacts: list[FreqArtifact] = field(default_factory=list)
    fit_low_cutoff: float = float("nan")
    fit_high_cutoff: float = float("nan")

    def summary(self) -> dict:
        return {
            "spectral_slope": self.spectral_slope,
            "effective_resolution_cycles_per_px": self.effective_resolution_cycles_per_px,
            "n_artifacts": len(self.artifacts),
            "artifact_kinds": [a.kind for a in self.artifacts],
            "max_artifact_severity": max((a.severity for a in self.artifacts), default=0.0),
        }


# ---------------------------------------------------------------------------- #
# A4.1 — Region extraction + windowing
# ---------------------------------------------------------------------------- #


def _largest_target_bbox(segmentation: np.ndarray, *, min_side: int = 64) -> tuple[int, int, int, int] | None:
    """Find the bounding box of the largest connected SEG_TARGET region.

    Returns (y0, y1, x0, x1) or None if no large enough target exists.
    """
    from scipy.ndimage import label, find_objects

    target = (segmentation == SEG_TARGET).astype(np.uint8)
    if not target.any():
        return None
    labeled, n = label(target)
    if n == 0:
        return None
    sizes = np.bincount(labeled.ravel())
    sizes[0] = 0  # background
    largest = int(np.argmax(sizes))
    if largest == 0:
        return None
    slices = find_objects(labeled)
    sl = slices[largest - 1]  # find_objects is 1-indexed
    if sl is None:
        return None
    y0, y1 = sl[0].start, sl[0].stop
    x0, x1 = sl[1].start, sl[1].stop
    if (y1 - y0) < min_side or (x1 - x0) < min_side:
        return None
    return y0, y1, x0, x1


def _square_crop(arr: np.ndarray, side: int) -> np.ndarray:
    h, w = arr.shape
    side = min(side, h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    return arr[y0 : y0 + side, x0 : x0 + side].copy()


def extract_target_window(
    image: np.ndarray, segmentation: np.ndarray, *, max_side: int = 512
) -> np.ndarray | None:
    """Cut and window the largest target region. Returns ``None`` if no good target."""
    bbox = _largest_target_bbox(segmentation)
    if bbox is None:
        return None
    y0, y1, x0, x1 = bbox
    region = image[y0:y1, x0:x1].astype(np.float64)
    side = min(max_side, region.shape[0], region.shape[1])
    region = _square_crop(region, side)
    region = region - float(np.mean(region))
    win = np.outer(signal.windows.tukey(side, alpha=0.25), signal.windows.tukey(side, alpha=0.25))
    return region * win


# ---------------------------------------------------------------------------- #
# A4.2 — Radial power spectrum
# ---------------------------------------------------------------------------- #


def radial_power_spectrum(
    windowed: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (frequencies in cycles/px, radial power)."""
    h, w = windowed.shape
    F = np.fft.fft2(windowed)
    psd = np.abs(F) ** 2
    radial = _radial_average(psd)
    # Frequency axis: bin 0 = DC, bin k corresponds to (k / N) cycles/px (square frame).
    n_bins = radial.size
    freqs = np.arange(n_bins) / float(min(h, w))
    return freqs, radial


# ---------------------------------------------------------------------------- #
# A4.3 — Slope fitting + effective resolution
# ---------------------------------------------------------------------------- #


def fit_power_law(
    freqs: np.ndarray, radial: np.ndarray, *, low_cut: float = 0.01, high_cut: float = 0.25
) -> tuple[float, float, float, float]:
    """Fit log(P) = slope * log(f) + intercept over the band [low_cut, high_cut].

    Returns ``(slope, intercept, low_used, high_used)``.
    """
    mask = (freqs >= low_cut) & (freqs <= high_cut) & (radial > 0)
    if mask.sum() < 4:
        return float("nan"), float("nan"), low_cut, high_cut
    log_f = np.log(freqs[mask])
    log_p = np.log(radial[mask])
    slope, intercept = np.polyfit(log_f, log_p, 1)
    return float(slope), float(intercept), float(low_cut), float(high_cut)


def estimate_effective_resolution(freqs: np.ndarray, radial: np.ndarray) -> float:
    """Frequency at which the power spectrum levels off into the noise floor.

    We define the noise floor as the median power above ``f = 0.4 cycles/px`` and
    find the lowest frequency above ``f = 0.05`` where smoothed power drops within
    1.5x of that floor.
    """
    if radial.size < 16:
        return float("nan")
    floor_band = (freqs >= 0.4)
    if not np.any(floor_band):
        return float("nan")
    floor = float(np.median(radial[floor_band]))
    if floor <= 0:
        return float("nan")
    # Smooth the radial profile and find the knee.
    smoothed = np.convolve(radial, np.ones(5) / 5, mode="same")
    candidates = np.where((freqs > 0.05) & (smoothed < 1.5 * floor))[0]
    if candidates.size == 0:
        return float("nan")
    return float(freqs[candidates[0]])


# ---------------------------------------------------------------------------- #
# A4.4 — Artifact detection
# ---------------------------------------------------------------------------- #


def detect_artifacts(
    freqs: np.ndarray,
    radial: np.ndarray,
    slope: float,
    intercept: float,
    *,
    fwhm_px: float | None,
    fit_low: float,
    fit_high: float,
    excess_db_threshold: float = 3.0,
) -> list[FreqArtifact]:
    """Compare the actual radial PSD to the fitted power law and flag bumps.

    - ``oversharpening``: any frequency in the mid-high band where PSD exceeds the
      power-law fit by ``excess_db_threshold`` dB or more.
    - ``ringing``: a periodic peak (sharp local maximum, prominence > threshold).
    - ``above_psf_cutoff``: significant PSD power above the optical PSF cutoff
      implied by ``fwhm_px`` (real detail can't exist there).
    """
    artifacts: list[FreqArtifact] = []
    if not np.isfinite(slope) or not np.isfinite(intercept):
        return artifacts

    # Rebuild the model PSD from the fit.
    safe_freqs = np.where(freqs > 0, freqs, 1e-9)
    model = np.exp(intercept + slope * np.log(safe_freqs))

    # Excess in dB, only over the fit band.
    band = (freqs >= fit_low) & (freqs <= fit_high) & (radial > 0)
    if not np.any(band):
        return artifacts

    excess_db = 10.0 * (np.log10(np.maximum(radial, 1e-30)) - np.log10(np.maximum(model, 1e-30)))

    # 1) Oversharpening: any mid-high band excess
    mid_high = (freqs >= 0.15) & (freqs <= fit_high) & band
    if np.any(mid_high):
        peak_idx = int(np.argmax(excess_db * mid_high))
        if excess_db[peak_idx] >= excess_db_threshold:
            severity = float(np.clip(excess_db[peak_idx] / 12.0, 0.0, 1.0))
            artifacts.append(
                FreqArtifact(
                    kind="oversharpening",
                    frequency_cycles_per_px=float(freqs[peak_idx]),
                    excess_db=float(excess_db[peak_idx]),
                    severity=severity,
                )
            )

    # 2) Ringing: scipy.signal.find_peaks on the residual (PSD - model)
    residual_db = excess_db.copy()
    residual_db[~band] = 0.0
    try:
        peaks, props = signal.find_peaks(residual_db, prominence=excess_db_threshold)
        for p_idx, prom in zip(peaks, props.get("prominences", [])):
            if freqs[p_idx] < 0.05:
                continue
            artifacts.append(
                FreqArtifact(
                    kind="ringing",
                    frequency_cycles_per_px=float(freqs[p_idx]),
                    excess_db=float(residual_db[p_idx]),
                    severity=float(np.clip(prom / 12.0, 0.0, 1.0)),
                )
            )
    except Exception:
        pass

    # 3) Above-PSF-cutoff: with FWHM_px, the diffraction-limited cutoff is roughly
    #    1 / (FWHM_px) cycles/px. Real detail cannot meaningfully exist above
    #    ~1.5 * that frequency.
    if fwhm_px and fwhm_px > 0:
        psf_cutoff = min(0.45, 1.0 / float(fwhm_px))
        above = (freqs > psf_cutoff) & (freqs < 0.45) & band
        if np.any(above):
            ratio_db = 10.0 * np.log10(np.mean(radial[above]) / max(np.median(radial[band]), 1e-30))
            if ratio_db > excess_db_threshold:
                artifacts.append(
                    FreqArtifact(
                        kind="above_psf_cutoff",
                        frequency_cycles_per_px=psf_cutoff,
                        excess_db=float(ratio_db),
                        severity=float(np.clip(ratio_db / 12.0, 0.0, 1.0)),
                    )
                )

    return artifacts


# ---------------------------------------------------------------------------- #
# Top-level A4 pipeline
# ---------------------------------------------------------------------------- #


def analyze_target_frequency(
    chars: ImageCharacterization,
    *,
    fwhm_px: float | None = None,
) -> TargetFreqResult:
    """Run A4.1 -> A4.4. Returns a result with NaN slope if no usable target region."""
    windowed = extract_target_window(chars.background_subtracted, chars.segmentation)
    if windowed is None:
        log.info("A4: no target region large enough for FFT")
        return TargetFreqResult(radial_power=np.zeros(0), radial_freq=np.zeros(0))

    freqs, radial = radial_power_spectrum(windowed)
    slope, intercept, fl, fh = fit_power_law(freqs, radial)
    eff_res = estimate_effective_resolution(freqs, radial)
    artifacts = detect_artifacts(
        freqs, radial, slope, intercept, fwhm_px=fwhm_px, fit_low=fl, fit_high=fh
    )

    log.info(
        "A4: slope=%.2f, eff_res=%.3f cyc/px, %d artifact(s)",
        slope, eff_res, len(artifacts),
    )

    return TargetFreqResult(
        radial_power=radial,
        radial_freq=freqs,
        spectral_slope=slope,
        spectral_intercept=intercept,
        effective_resolution_cycles_per_px=eff_res,
        artifacts=artifacts,
        fit_low_cutoff=fl,
        fit_high_cutoff=fh,
    )


__all__ = [
    "FreqArtifact",
    "TargetFreqResult",
    "analyze_target_frequency",
    "detect_artifacts",
    "estimate_effective_resolution",
    "extract_target_window",
    "fit_power_law",
    "radial_power_spectrum",
]
