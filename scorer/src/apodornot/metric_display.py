"""Display metadata for the scoring metrics — human labels, units, and number-formatting hints.

Kept separate from ``SCORING_METRICS`` so the scientific names stay precise and
self-documenting in code, while the UI gets short, recognizable labels in line
with the React mockup. The ``format`` hint tells the UI how to render the
numeric value (units, scientific notation, decimals).
"""

from __future__ import annotations

from typing import TypedDict


class MetricDisplay(TypedDict):
    label: str
    unit: str
    format: str  # "px" | "decimal" | "scientific" | "auto"


METRIC_DISPLAY: dict[str, MetricDisplay] = {
    # Star quality
    "median_fwhm_px":            {"label": "Median FWHM",        "unit": "px",     "format": "decimal"},
    "median_eccentricity":       {"label": "Median eccentricity","unit": "",       "format": "decimal"},
    "fwhm_corner_excess":        {"label": "Corner FWHM excess", "unit": "",       "format": "decimal"},
    "fwhm_quadrant_asymmetry":   {"label": "FWHM quadrant asymmetry", "unit": "", "format": "decimal"},

    # Noise
    "noise_floor_l":             {"label": "Background σ",       "unit": "",       "format": "scientific"},
    "psd_spectral_slope":        {"label": "Noise PSD slope",    "unit": "",       "format": "decimal"},
    "psd_high_band_suppression": {"label": "HF suppression",     "unit": "",       "format": "decimal"},
    "autocorr_width_px":         {"label": "Noise autocorr",     "unit": "px",     "format": "decimal"},
    "snr_target_median":         {"label": "Median SNR",         "unit": "",       "format": "decimal"},
    "fpn_max_pattern":           {"label": "Fixed-pattern noise","unit": "",       "format": "decimal"},

    # Target frequency
    "target_spectral_slope":     {"label": "Detail slope",       "unit": "",       "format": "decimal"},
    "target_effective_resolution":{"label": "Effective res.",    "unit": "cy/px",  "format": "decimal"},

    # Gradient / calibration
    "gradient_ratio":            {"label": "Gradient ratio",     "unit": "× σ",    "format": "decimal"},
    "vignetting_falloff":        {"label": "Vignetting",         "unit": "",       "format": "decimal"},
    "color_balance_magnitude":   {"label": "Channel balance",    "unit": "",       "format": "decimal"},

    # Color
    "color_overall_score":       {"label": "Color overall",      "unit": "",       "format": "decimal"},
    "star_diversity_score":      {"label": "Stellar chroma",     "unit": "",       "format": "decimal"},
    "background_chroma_distance":{"label": "Background chroma",  "unit": "",       "format": "decimal"},
    "chroma_concentration":      {"label": "Chroma concentration","unit": "",      "format": "decimal"},
}


def display_for(metric: str) -> MetricDisplay:
    """Return display metadata for ``metric``, falling back to a generic entry."""
    return METRIC_DISPLAY.get(
        metric,
        {"label": metric.replace("_", " "), "unit": "", "format": "auto"},
    )


__all__ = ["METRIC_DISPLAY", "MetricDisplay", "display_for"]
