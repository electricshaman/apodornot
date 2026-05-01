"""Per-stage diagnostic data extraction for UI visualizations.

The scorecard JSON's ``diagnostics`` key is built here. Each axis gets a small
JSON-friendly payload that the UI can render as a per-axis visualization
(eccentricity vector field, noise histograms, radial PSD curve, background
heatmap, stellar color-magnitude diagram).

Wire format is small (~10-20 KB per scorecard): per-star records are subsampled
to 200 if more were fit, the radial PSD is decimated to 100 points, the
background map is downsampled to a 28x11 grid.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .image_chars import ImageCharacterization
from .noise import NoiseResult
from .star_field import StarFieldResult
from .target_freq import TargetFreqResult


# Channel display colors used by the UI noise histograms.
_CHANNEL_COLORS = {"R": "#fb7185", "G": "#7dd3fc", "B": "#a78bfa", "L": "#cbd5e1"}


def build_diagnostics(
    *,
    chars: ImageCharacterization,
    star_field: StarFieldResult,
    noise: NoiseResult,
    target_freq: TargetFreqResult,
) -> dict[str, Any]:
    """Build the scorecard's ``diagnostics`` payload from the A1–A6 results."""
    return {
        "star_field": _star_field_diag(star_field, chars),
        "noise": _noise_diag(noise),
        "target_psd": _target_psd_diag(target_freq),
        "gradient": _gradient_diag(chars),
        "color_cmd": _color_cmd_diag(star_field),
    }


# ---------------------------------------------------------------------------- #
# Star quality — eccentricity vector field
# ---------------------------------------------------------------------------- #


def _star_field_diag(sf: StarFieldResult, chars: ImageCharacterization) -> dict[str, Any]:
    if not sf.measurements:
        return {"n_fitted": 0, "image_w": chars.width, "image_h": chars.height, "stars": []}

    # Subsample if needed to keep wire size bounded — 200 is plenty for a vector field.
    measurements = sf.measurements
    if len(measurements) > 200:
        rng = np.random.default_rng(0xA90D)
        idx = rng.choice(len(measurements), 200, replace=False)
        measurements = [measurements[i] for i in sorted(idx)]

    fv = sf.field_variation
    return {
        "n_fitted": len(sf.measurements),
        "image_w": chars.width,
        "image_h": chars.height,
        "median_ecc": fv.median_eccentricity if fv else None,
        "ecc_pattern": fv.eccentricity_pattern if fv else None,
        "pa_concentration": fv.pa_concentration if fv else None,
        "stars": [
            {
                "x": float(m.x),
                "y": float(m.y),
                "ecc": float(m.eccentricity),
                "pa_deg": float(m.pa_deg),
                "fwhm": float(m.fwhm),
            }
            for m in measurements
        ],
    }


# ---------------------------------------------------------------------------- #
# Noise — per-channel histograms (just mean + std; UI draws Gaussians)
# ---------------------------------------------------------------------------- #


def _noise_diag(noise: NoiseResult) -> dict[str, Any]:
    return {
        "channels": [
            {
                "name": ch.name,
                "color": _CHANNEL_COLORS.get(ch.name, "#cbd5e1"),
                "mean": float(ch.mean) if math.isfinite(ch.mean) else None,
                "std": float(ch.noise_floor) if math.isfinite(ch.noise_floor) else None,
                "skewness": float(ch.skewness) if math.isfinite(ch.skewness) else None,
                "kurtosis": float(ch.kurtosis) if math.isfinite(ch.kurtosis) else None,
            }
            for ch in noise.channels
        ],
        "autocorr_width_px": float(noise.autocorr_width_px)
        if math.isfinite(noise.autocorr_width_px)
        else None,
        "snr_target_median": float(noise.snr_target)
        if math.isfinite(noise.snr_target)
        else None,
    }


# ---------------------------------------------------------------------------- #
# Detail resolution — radial power spectrum
# ---------------------------------------------------------------------------- #


def _target_psd_diag(tf: TargetFreqResult) -> dict[str, Any]:
    if tf.radial_freq.size == 0:
        return {
            "freq": [],
            "power": [],
            "spectral_slope": None,
            "effective_resolution_cyc_per_px": None,
            "artifacts": [],
        }

    freq = np.asarray(tf.radial_freq)
    power = np.asarray(tf.radial_power)

    # Decimate to ~100 evenly-spaced log-frequency bins.
    n_bins = min(100, len(freq))
    if len(freq) > n_bins:
        idx = np.linspace(0, len(freq) - 1, n_bins).astype(int)
        freq = freq[idx]
        power = power[idx]

    # Drop the DC bin to avoid log issues; clamp tiny power.
    if len(freq) > 0 and freq[0] == 0:
        freq = freq[1:]
        power = power[1:]
    power = np.maximum(power, 1e-30)

    return {
        "freq": [float(f) for f in freq.tolist()],
        "power": [float(p) for p in power.tolist()],
        "spectral_slope": float(tf.spectral_slope) if math.isfinite(tf.spectral_slope) else None,
        "effective_resolution_cyc_per_px": float(tf.effective_resolution_cycles_per_px)
        if math.isfinite(tf.effective_resolution_cycles_per_px)
        else None,
        "artifacts": [
            {
                "kind": a.kind,
                "freq": float(a.frequency_cycles_per_px),
                "excess_db": float(a.excess_db),
                "severity": float(a.severity),
            }
            for a in tf.artifacts
        ],
    }


# ---------------------------------------------------------------------------- #
# Gradient control — background heatmap (downsampled)
# ---------------------------------------------------------------------------- #


def _gradient_diag(chars: ImageCharacterization, *, target_cols: int = 28, target_rows: int = 11) -> dict[str, Any]:
    bg = np.asarray(chars.background)
    h, w = bg.shape
    # Block-mean downsample to (target_rows, target_cols).
    rows = min(target_rows, h)
    cols = min(target_cols, w)
    row_edges = np.linspace(0, h, rows + 1).astype(int)
    col_edges = np.linspace(0, w, cols + 1).astype(int)

    grid = np.zeros((rows, cols), dtype=np.float64)
    for j in range(rows):
        for i in range(cols):
            cell = bg[row_edges[j] : row_edges[j + 1], col_edges[i] : col_edges[i + 1]]
            if cell.size > 0:
                grid[j, i] = float(cell.mean())

    # Normalize to [0, 1] for color-mapping in the UI.
    g_min = float(grid.min())
    g_max = float(grid.max())
    if g_max - g_min > 1e-9:
        grid_norm = (grid - g_min) / (g_max - g_min)
    else:
        grid_norm = np.full_like(grid, 0.5)

    return {
        "cols": cols,
        "rows": rows,
        "values": [[float(v) for v in row] for row in grid_norm.tolist()],
        "raw_min": g_min,
        "raw_max": g_max,
    }


# ---------------------------------------------------------------------------- #
# Color calibration — stellar color-magnitude diagram
# ---------------------------------------------------------------------------- #


def _color_cmd_diag(sf: StarFieldResult) -> dict[str, Any]:
    """Map per-star RGB to a B-V proxy and an instrumental magnitude.

    Real B-V requires photometric calibration we don't have, so we use:
        bv_proxy = log10(R / B), normalized to roughly [-0.3, 1.8]
        mag      = -2.5 * log10(R + G + B), offset so brightest = 8

    The UI uses these to position stars in CMD space and derive a display
    color from the B-V proxy (blue → white → yellow → orange → red).
    """
    stars = []
    fluxes = []
    for m in sf.measurements:
        if m.star_color_rgb is None:
            continue
        r, g, b = m.star_color_rgb
        if r <= 0 or b <= 0:
            continue
        bv_proxy = math.log10(max(r, 1e-9) / max(b, 1e-9))
        # Clamp to a reasonable display range.
        bv_proxy = max(-0.3, min(1.8, bv_proxy))
        total_flux = max(r + g + b, 1e-9)
        fluxes.append(total_flux)
        stars.append(
            {
                "bv_proxy": float(bv_proxy),
                "flux": float(total_flux),
                "rgb": [float(r), float(g), float(b)],
            }
        )

    if not stars:
        return {"stars": [], "n": 0}

    # Convert flux → magnitude with the brightest star at mag 8 for display.
    max_flux = max(fluxes)
    for s in stars:
        s["mag"] = float(-2.5 * math.log10(s["flux"] / max_flux) + 8.0)
        del s["flux"]

    return {"stars": stars, "n": len(stars)}


__all__ = ["build_diagnostics"]
