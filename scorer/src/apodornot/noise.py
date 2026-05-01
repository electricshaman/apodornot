"""A3 — Noise characterization.

Measures the noise floor (per-channel std), distribution shape (skewness /
kurtosis / normality), the power spectral density (PSD) shape across spatial
frequency bands (a fingerprint for noise reduction or oversharpening), the
autocorrelation width (smearing from drizzle / resampling / NR), and target-region
SNR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import signal, stats

from .image_chars import SEG_BACKGROUND, SEG_TARGET, ImageCharacterization
from .logging import get_logger

log = get_logger("apodornot.noise")


# ---------------------------------------------------------------------------- #
# Data model
# ---------------------------------------------------------------------------- #


@dataclass
class ChannelNoiseStats:
    name: str
    noise_floor: float        # std-dev of background patches
    mean: float
    skewness: float
    kurtosis: float            # excess kurtosis (Gaussian = 0)
    normality_p: float         # scipy.stats.normaltest p-value (low = non-Gaussian)


@dataclass
class PSDShape:
    spectral_slope: float      # log-log slope of radial PSD
    low_band_power: float
    mid_band_power: float
    high_band_power: float
    low_to_high_ratio: float
    high_band_suppression: float  # 1 - high/expected_white = how suppressed vs white noise


@dataclass
class FixedPatternNoise:
    """Row/column-aligned noise signature (amp glow, dark-current striping,
    residual flat-field issues). Radial PSD and 2D autocorrelation miss this
    because the pattern is anisotropic — energy only along one axis.

    For each background patch we collapse to:
      - column mean (length W) -> row-aligned pattern energy
      - row mean (length H)    -> column-aligned pattern energy
    Then PSD each. Excess low-frequency energy in either signature flags
    fixed-pattern noise.
    """
    column_pattern_score: float  # 0..1, fraction of energy in the lowest 25% of frequencies
    row_pattern_score: float
    column_amplitude: float      # std-dev of the column-mean signal
    row_amplitude: float


@dataclass
class NoiseResult:
    channels: list[ChannelNoiseStats]
    psd: PSDShape | None
    autocorr_width_px: float
    snr_target: float
    snr_target_pct90: float
    n_background_patches: int
    snr_per_quadrant: dict[str, float] = field(default_factory=dict)  # tl/tr/bl/br medians
    fixed_pattern: FixedPatternNoise | None = None
    raw: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "noise_floors": {c.name: c.noise_floor for c in self.channels},
            "skewness": {c.name: c.skewness for c in self.channels},
            "kurtosis": {c.name: c.kurtosis for c in self.channels},
            "normality_p": {c.name: c.normality_p for c in self.channels},
            "psd_spectral_slope": self.psd.spectral_slope if self.psd else None,
            "psd_low_to_high_ratio": self.psd.low_to_high_ratio if self.psd else None,
            "psd_high_band_suppression": self.psd.high_band_suppression if self.psd else None,
            "autocorr_width_px": self.autocorr_width_px,
            "snr_target_median": self.snr_target,
            "snr_target_p90": self.snr_target_pct90,
            "snr_per_quadrant": self.snr_per_quadrant,
            "fpn_row_pattern": self.fixed_pattern.row_pattern_score if self.fixed_pattern else None,
            "fpn_column_pattern": self.fixed_pattern.column_pattern_score if self.fixed_pattern else None,
            "fpn_max_pattern": (
                max(self.fixed_pattern.row_pattern_score, self.fixed_pattern.column_pattern_score)
                if self.fixed_pattern else None
            ),
            "n_background_patches": self.n_background_patches,
        }


# ---------------------------------------------------------------------------- #
# A3.1 — Background patch statistics
# ---------------------------------------------------------------------------- #


def _channel_arrays(chars: ImageCharacterization) -> list[tuple[str, np.ndarray]]:
    if chars.color is not None:
        return [
            ("R", chars.color[..., 0]),
            ("G", chars.color[..., 1]),
            ("B", chars.color[..., 2]),
            ("L", chars.luminance),
        ]
    return [("L", chars.luminance)]


def _background_pixels(channel: np.ndarray, segmentation: np.ndarray) -> np.ndarray:
    return channel[segmentation == SEG_BACKGROUND]


def channel_stats(channel: np.ndarray, segmentation: np.ndarray, name: str) -> ChannelNoiseStats:
    """Compute mean / std / skew / kurtosis / normality on background pixels only."""
    bg = _background_pixels(channel, segmentation)
    if bg.size < 100:
        return ChannelNoiseStats(
            name=name,
            noise_floor=float("nan"),
            mean=float("nan"),
            skewness=float("nan"),
            kurtosis=float("nan"),
            normality_p=float("nan"),
        )

    # Sigma-clip a couple of times to drop residual cosmic rays / source bleed.
    bg = bg.astype(np.float64)
    for _ in range(2):
        med = float(np.median(bg))
        mad = float(np.median(np.abs(bg - med)))
        if mad <= 0:
            break
        bg = bg[np.abs(bg - med) < 5.0 * 1.4826 * mad]
        if bg.size < 100:
            break

    mean = float(np.mean(bg))
    std = float(np.std(bg))
    skew = float(stats.skew(bg))
    kurt = float(stats.kurtosis(bg))  # Fisher (excess) kurtosis by default

    # Normality test on a random sample (full array kills CPU).
    rng = np.random.default_rng(0xA90D)
    sample = bg if bg.size <= 5000 else rng.choice(bg, size=5000, replace=False)
    try:
        _, p = stats.normaltest(sample)
        p = float(p)
    except ValueError:
        p = float("nan")

    return ChannelNoiseStats(
        name=name, noise_floor=std, mean=mean, skewness=skew, kurtosis=kurt, normality_p=p
    )


# ---------------------------------------------------------------------------- #
# Background patch sampling
# ---------------------------------------------------------------------------- #


def sample_background_patches(
    image: np.ndarray,
    segmentation: np.ndarray,
    *,
    patch_size: int = 64,
    max_patches: int = 12,
    min_background_fraction: float = 0.95,
    rng_seed: int = 0xA90D,
) -> list[np.ndarray]:
    """Find up to ``max_patches`` patches that are >= ``min_background_fraction`` background."""
    h, w = image.shape[:2]
    rng = np.random.default_rng(rng_seed)
    patches: list[np.ndarray] = []
    attempts = 0
    while len(patches) < max_patches and attempts < max_patches * 50:
        attempts += 1
        x = int(rng.integers(0, w - patch_size))
        y = int(rng.integers(0, h - patch_size))
        seg_patch = segmentation[y : y + patch_size, x : x + patch_size]
        if (seg_patch == SEG_BACKGROUND).mean() < min_background_fraction:
            continue
        patches.append(image[y : y + patch_size, x : x + patch_size].copy())
    return patches


# ---------------------------------------------------------------------------- #
# A3.2 — Power spectral density
# ---------------------------------------------------------------------------- #


def compute_psd_shape(patches: list[np.ndarray]) -> PSDShape | None:
    """Average PSDs of background patches and characterize the spectral shape."""
    if not patches:
        return None

    avg_radial: np.ndarray | None = None
    n = 0
    for patch in patches:
        p = patch.astype(np.float64) - float(np.mean(patch))
        # Hann window to suppress spectral leakage.
        win = np.outer(signal.windows.hann(p.shape[0]), signal.windows.hann(p.shape[1]))
        p = p * win
        F = np.fft.fft2(p)
        psd = np.abs(F) ** 2

        radial = _radial_average(psd)
        if avg_radial is None:
            avg_radial = radial
        else:
            # Lengths can differ if patch shapes differ — pad/truncate.
            m = min(len(avg_radial), len(radial))
            avg_radial = avg_radial[:m] + radial[:m]
        n += 1
    if avg_radial is None or n == 0:
        return None
    avg_radial = avg_radial / n

    # Drop the DC bin (index 0).
    radial = avg_radial[1:]
    if radial.size < 16:
        return PSDShape(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    nbins = radial.size
    lo_end = max(2, nbins // 8)
    mid_end = max(lo_end + 1, nbins // 2)

    low_power = float(np.mean(radial[:lo_end]))
    mid_power = float(np.mean(radial[lo_end:mid_end]))
    high_power = float(np.mean(radial[mid_end:]))
    low_to_high = low_power / max(high_power, 1e-30)

    # log-log slope across the full range (DC excluded)
    freqs = np.arange(1, nbins + 1)
    log_f = np.log(freqs)
    log_p = np.log(np.maximum(radial, 1e-30))
    slope = float(np.polyfit(log_f, log_p, 1)[0])

    # Reference: white noise has flat PSD, so high/low ~ 1. Suppression > 0 means
    # the high band is reduced compared to mid (typical NR signature).
    suppression = float(max(0.0, 1.0 - high_power / max(mid_power, 1e-30)))

    return PSDShape(
        spectral_slope=slope,
        low_band_power=low_power,
        mid_band_power=mid_power,
        high_band_power=high_power,
        low_to_high_ratio=low_to_high,
        high_band_suppression=suppression,
    )


def _radial_average(psd_2d: np.ndarray) -> np.ndarray:
    """Azimuthally average a 2D PSD into a 1D radial profile."""
    h, w = psd_2d.shape
    psd_shifted = np.fft.fftshift(psd_2d)
    cy, cx = h / 2.0, w / 2.0
    yy, xx = np.indices(psd_2d.shape)
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    r_int = r.astype(np.int32)
    n_bins = int(min(cy, cx)) + 1
    sums = np.bincount(r_int.ravel(), weights=psd_shifted.ravel(), minlength=n_bins)[:n_bins]
    counts = np.bincount(r_int.ravel(), minlength=n_bins)[:n_bins]
    radial = np.zeros(n_bins, dtype=np.float64)
    nonzero = counts > 0
    radial[nonzero] = sums[nonzero] / counts[nonzero]
    return radial


# ---------------------------------------------------------------------------- #
# A3.3 — Autocorrelation
# ---------------------------------------------------------------------------- #


def autocorrelation_width(patches: list[np.ndarray]) -> float:
    """Return mean half-width-at-half-max of the noise autocorrelation, in pixels.

    For ideal uncorrelated noise this is < 1. Wider peaks indicate processing-
    induced smearing (drizzle, NR, resampling).
    """
    if not patches:
        return float("nan")
    widths: list[float] = []
    for patch in patches:
        p = patch.astype(np.float64) - float(np.mean(patch))
        # Normalize energy so peak is 1.
        denom = float(np.sum(p * p))
        if denom <= 0:
            continue
        F = np.fft.fft2(p)
        ac = np.real(np.fft.ifft2(F * np.conj(F)))
        ac /= denom
        ac = np.fft.fftshift(ac)
        h, w = ac.shape
        cy, cx = h // 2, w // 2
        # Look at a small central neighborhood — measure radius at which AC drops to 0.5.
        max_r = min(h, w) // 4
        yy, xx = np.indices(ac.shape)
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        for radius in range(1, max_r):
            mask = (r >= radius - 0.5) & (r < radius + 0.5)
            if mask.sum() < 4:
                continue
            mean_ac = float(np.mean(ac[mask]))
            if mean_ac < 0.5:
                widths.append(float(radius))
                break
        else:
            widths.append(float(max_r))
    if not widths:
        return float("nan")
    return float(np.mean(widths))


# ---------------------------------------------------------------------------- #
# A3.4 — SNR estimation in target regions
# ---------------------------------------------------------------------------- #


def estimate_target_snr(
    bg_subtracted: np.ndarray, rms: np.ndarray, segmentation: np.ndarray
) -> tuple[float, float]:
    """Return (median, 90th percentile) SNR over target-mask pixels."""
    target = segmentation == SEG_TARGET
    if not np.any(target):
        return float("nan"), float("nan")
    sig = bg_subtracted[target]
    n = rms[target]
    n = np.where(n > 0, n, 1e-9)
    snr = sig / n
    return float(np.median(snr)), float(np.percentile(snr, 90))


def detect_fixed_pattern_noise(patches: list[np.ndarray]) -> FixedPatternNoise | None:
    """Compute row-aligned and column-aligned noise pattern scores.

    For each background patch:
      - row_mean[i] = mean of pixels in row i; PSD on this 1D signal reveals
        any column-aligned periodic structure (vertical stripes).
      - col_mean[j] = mean of pixels in column j; PSD reveals row-aligned
        structure (horizontal stripes / amp glow).

    Score = fraction of total PSD energy in the lowest 25% of frequencies
    (excluding DC). White noise → ~0.25 (uniform). Strong fixed pattern → ~0.6+.
    """
    if not patches:
        return None
    row_scores: list[float] = []
    col_scores: list[float] = []
    row_amps: list[float] = []
    col_amps: list[float] = []

    for patch in patches:
        p = patch.astype(np.float64)
        p = p - float(np.mean(p))
        row_mean = p.mean(axis=1)  # avg over columns → length H
        col_mean = p.mean(axis=0)  # avg over rows    → length W
        row_amps.append(float(np.std(row_mean)))
        col_amps.append(float(np.std(col_mean)))

        for sig, score_list in [(row_mean, col_scores), (col_mean, row_scores)]:
            f = np.fft.rfft(sig)
            psd = np.abs(f) ** 2
            if psd.size <= 1:
                continue
            psd = psd[1:]  # drop DC
            if float(psd.sum()) <= 0:
                continue
            cutoff = max(1, int(len(psd) * 0.25))
            score_list.append(float(psd[:cutoff].sum() / psd.sum()))

    if not row_scores or not col_scores:
        return None
    return FixedPatternNoise(
        column_pattern_score=float(np.mean(col_scores)),
        row_pattern_score=float(np.mean(row_scores)),
        column_amplitude=float(np.mean(col_amps)),
        row_amplitude=float(np.mean(row_amps)),
    )


def estimate_quadrant_snr(
    bg_subtracted: np.ndarray, rms: np.ndarray, segmentation: np.ndarray
) -> dict[str, float]:
    """Median target SNR per quadrant (tl/tr/bl/br). NaN if no target pixels in a quadrant.

    Reveals uneven SNR — a smaller, brighter target offset toward one corner,
    asymmetric calibration, or a per-quadrant amp-glow residual all show up as
    a >2x asymmetry across quadrants.
    """
    h, w = bg_subtracted.shape
    cy, cx = h // 2, w // 2
    target = segmentation == SEG_TARGET

    out: dict[str, float] = {}
    for label, ys, xs in [
        ("tl", slice(0, cy), slice(0, cx)),
        ("tr", slice(0, cy), slice(cx, w)),
        ("bl", slice(cy, h), slice(0, cx)),
        ("br", slice(cy, h), slice(cx, w)),
    ]:
        sub_target = target[ys, xs]
        if not np.any(sub_target):
            out[label] = float("nan")
            continue
        sig = bg_subtracted[ys, xs][sub_target]
        n = rms[ys, xs][sub_target]
        n = np.where(n > 0, n, 1e-9)
        out[label] = float(np.median(sig / n))
    return out


# ---------------------------------------------------------------------------- #
# Top-level A3 pipeline
# ---------------------------------------------------------------------------- #


def characterize_noise(chars: ImageCharacterization, *, max_patches: int = 12) -> NoiseResult:
    """Run A3.1 -> A3.4 against an A1-output ``ImageCharacterization``."""
    channel_results: list[ChannelNoiseStats] = []
    for name, ch in _channel_arrays(chars):
        channel_results.append(channel_stats(ch, chars.segmentation, name))

    patches = sample_background_patches(
        chars.luminance, chars.segmentation, max_patches=max_patches
    )
    psd = compute_psd_shape(patches)
    ac_width = autocorrelation_width(patches)
    snr_med, snr_p90 = estimate_target_snr(chars.background_subtracted, chars.rms, chars.segmentation)
    snr_per_quadrant = estimate_quadrant_snr(chars.background_subtracted, chars.rms, chars.segmentation)
    fixed_pattern = detect_fixed_pattern_noise(patches)

    log.info(
        "A3: %d bg patches, slope=%.2f, ac_width=%.2f px, SNR_median=%.2f",
        len(patches),
        (psd.spectral_slope if psd else float("nan")),
        ac_width,
        snr_med,
    )

    return NoiseResult(
        channels=channel_results,
        psd=psd,
        autocorr_width_px=ac_width,
        snr_target=snr_med,
        snr_target_pct90=snr_p90,
        n_background_patches=len(patches),
        snr_per_quadrant=snr_per_quadrant,
        fixed_pattern=fixed_pattern,
    )


__all__ = [
    "ChannelNoiseStats",
    "NoiseResult",
    "PSDShape",
    "autocorrelation_width",
    "channel_stats",
    "characterize_noise",
    "compute_psd_shape",
    "estimate_target_snr",
    "sample_background_patches",
]
