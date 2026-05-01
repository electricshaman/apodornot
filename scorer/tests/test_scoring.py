"""Tests for A7 — scoring."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from apodornot.archive_pipeline import (
    ArchiveDistributions,
    MetricDistribution,
    load_distributions,
)
from apodornot.scoring import (
    aggregate_axes,
    load_default_distributions,
    score_metrics,
    select_reference_set,
)


# ---------------------------------------------------------------------------- #
# Synthetic reference distribution helpers
# ---------------------------------------------------------------------------- #


def _build_dists():
    """A toy ArchiveDistributions covering the metrics referenced in axes."""
    metrics = {}
    # Build small uniform distributions for every metric in SCORING_METRICS.
    from apodornot.archive_pipeline import SCORING_METRICS

    rng = np.random.default_rng(0)
    for name, _path, hib in SCORING_METRICS:
        values = list(rng.uniform(0.0, 1.0, 50))
        metrics[name] = MetricDistribution(
            metric=name,
            higher_is_better=hib,
            n=len(values),
            values=values,
            quantiles={
                "p10": float(np.percentile(values, 10)),
                "p25": float(np.percentile(values, 25)),
                "p50": float(np.percentile(values, 50)),
                "p75": float(np.percentile(values, 75)),
                "p90": float(np.percentile(values, 90)),
            },
        )
    return ArchiveDistributions(by_category={"galaxy": metrics, "global": metrics})


# ---------------------------------------------------------------------------- #
# Reference selection
# ---------------------------------------------------------------------------- #


def test_select_reference_falls_back_to_global_for_unknown_category():
    dists = _build_dists()
    cat, metrics = select_reference_set(dists, target_type="planetary_nebula")
    assert cat == "global"


def test_select_reference_uses_specific_category_when_present():
    dists = _build_dists()
    cat, metrics = select_reference_set(dists, target_type="galaxy")
    assert cat == "galaxy"


def test_select_reference_falls_back_when_too_few_samples():
    dists = _build_dists()
    # Trim galaxy to 2 entries
    for m in dists.by_category["galaxy"].values():
        m.n = 2
        m.values = m.values[:2]
    cat, _ = select_reference_set(dists, target_type="galaxy", min_n=8)
    assert cat == "global"


# ---------------------------------------------------------------------------- #
# Metric scoring direction
# ---------------------------------------------------------------------------- #


def test_score_metrics_lower_is_better_high_value_low_percentile():
    """A high gradient_ratio should score in the bottom percentile (lower is better)."""
    dists = _build_dists()
    summary = {"calibration": {"gradient_ratio": 1.0}}  # max value -> bottom percentile
    scores = score_metrics(summary, dists.by_category["global"])
    g = next(s for s in scores if s.metric == "gradient_ratio")
    assert g.percentile < 20  # very poor


def test_score_metrics_higher_is_better_high_value_high_percentile():
    """A high SNR should score in the top percentile (higher is better)."""
    dists = _build_dists()
    summary = {"noise": {"snr_target_median": 1.0}}
    scores = score_metrics(summary, dists.by_category["global"])
    snr = next(s for s in scores if s.metric == "snr_target_median")
    assert snr.percentile > 80


def test_score_metrics_handles_missing_value():
    dists = _build_dists()
    scores = score_metrics({}, dists.by_category["global"])
    assert all(np.isnan(s.percentile) for s in scores)


# ---------------------------------------------------------------------------- #
# Axis aggregation
# ---------------------------------------------------------------------------- #


def test_aggregate_axes_produces_5_axes():
    dists = _build_dists()
    summary = {
        "star_field": {"median_fwhm_px": 0.5, "median_eccentricity": 0.5, "fwhm_corner_excess": 0.5},
        "noise": {
            "noise_floors": {"L": 0.5},
            "psd_high_band_suppression": 0.5,
            "autocorr_width_px": 0.5,
            "snr_target_median": 0.5,
            "psd_spectral_slope": 0.5,
        },
        "target_freq": {"spectral_slope": 0.5, "effective_resolution_cycles_per_px": 0.5},
        "calibration": {
            "gradient_ratio": 0.5,
            "vignetting_falloff": 0.5,
            "color_balance_magnitude": 0.5,
        },
        "color": {
            "overall_score": 0.5,
            "star_diversity_score": 0.5,
            "background_chroma_distance": 0.5,
        },
    }
    scores = score_metrics(summary, dists.by_category["global"])
    axes = aggregate_axes(scores)
    assert len(axes) == 5
    assert {ax.axis for ax in axes} == {
        "Star quality",
        "Noise management",
        "Detail resolution",
        "Gradient control",
        "Color calibration",
    }
    for ax in axes:
        # Median value -> percentile ~50 -> axis ~50
        assert 30 < ax.score < 70


# ---------------------------------------------------------------------------- #
# Default distributions are bundled
# ---------------------------------------------------------------------------- #


def test_default_distributions_bundle_loads():
    dists = load_default_distributions()
    assert dists is not None
    assert "global" in dists.by_category
    # Every SCORING_METRICS entry should be present in the global pool.
    from apodornot.archive_pipeline import SCORING_METRICS

    for name, _, _ in SCORING_METRICS:
        assert name in dists.by_category["global"]


# ---------------------------------------------------------------------------- #
# End-to-end on a real APOD image
# ---------------------------------------------------------------------------- #


APOD_FIXTURE = Path("apod_archive/2024/2024-01-15.jpg")


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_score_evaluation_real_image_end_to_end(tmp_path):
    from apodornot.pipeline import evaluate_image
    from apodornot.scoring import render_radar_chart, score_evaluation

    eval_result = evaluate_image(APOD_FIXTURE)
    scorecard = score_evaluation(eval_result)
    assert 0.0 <= scorecard.overall_score <= 100.0
    assert len(scorecard.axis_scores) == 5
    # Render the chart and confirm a PNG was produced.
    out = render_radar_chart(scorecard, tmp_path / "radar.png")
    assert out.exists() and out.stat().st_size > 1000
