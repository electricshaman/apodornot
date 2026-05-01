"""Tests for A8 — archive pipeline (categorization, indexing, distribution building)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from apodornot.archive_pipeline import (
    SCORING_METRICS,
    build_archive_index,
    build_distributions,
    categorize_entry,
    evaluation_cache_path,
    load_distributions,
    save_distributions,
)


# ---------------------------------------------------------------------------- #
# Categorization (A8.3)
# ---------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "title,expected",
    [
        ("M31: The Andromeda Galaxy", "galaxy"),
        ("The Orion Nebula", "orion_nebula"),
        ("NGC 2244: A Star Cluster in the Rosette Nebula", "rosette"),
        ("Horsehead Dark Nebula", "horsehead"),
        ("Helix Nebula", "planetary_nebula"),
        ("Veil Nebula", "supernova_remnant"),
        ("Pleiades Star Cluster", "open_cluster"),
        ("M13: A Globular Cluster", "globular_cluster"),
        ("Mars at Opposition", "solar_system"),
        ("Comet C/2024", "comet"),
        ("Aurora Borealis Over Iceland", "aurora"),
        ("A Random Title with No Keywords", "uncategorized"),
    ],
)
def test_categorize_entry(title, expected):
    assert categorize_entry(title) == expected


# ---------------------------------------------------------------------------- #
# Archive indexing
# ---------------------------------------------------------------------------- #


def _make_archive(tmp_path, entries):
    for date, title, classification in entries:
        year_dir = tmp_path / date[:4]
        year_dir.mkdir(parents=True, exist_ok=True)
        img = year_dir / f"{date}.jpg"
        img.write_bytes(b"fake")
        sidecar = year_dir / f"{date}.jpg.json"
        sidecar.write_text(
            json.dumps(
                {
                    "date": date,
                    "title": title,
                    "explanation": "",
                    "media_type": "image" if classification != "video" else "video",
                    "classification": classification,
                }
            )
        )
        if classification == "video":
            video_sidecar = year_dir / f"{date}.video.json"
            video_sidecar.write_text(json.dumps({"date": date, "classification": "video"}))


def test_build_archive_index_skips_videos_and_non_astrophotos(tmp_path):
    _make_archive(
        tmp_path,
        [
            ("2024-01-01", "M31 Andromeda", "astrophoto"),
            ("2024-01-02", "Cool video", "video"),
            ("2024-01-03", "Diagram of orbits", "non_astrophoto"),
            ("2024-01-04", "Orion Nebula", "astrophoto"),
        ],
    )
    entries = build_archive_index(tmp_path)
    assert {e.image_path.stem for e in entries} == {"2024-01-01", "2024-01-04"}
    assert {e.category for e in entries} == {"galaxy", "orion_nebula"}


def test_build_archive_index_handles_missing_dir(tmp_path):
    out = build_archive_index(tmp_path / "nope")
    assert out == []


# ---------------------------------------------------------------------------- #
# Distribution building (A8.4)
# ---------------------------------------------------------------------------- #


def _write_eval_cache(cache_dir: Path, image_path: Path, summary: dict):
    cache_dir.mkdir(parents=True, exist_ok=True)
    eval_path = evaluation_cache_path(cache_dir, image_path)
    eval_path.write_text(json.dumps(summary))


def test_build_distributions_aggregates_by_category(tmp_path):
    archive = tmp_path / "archive"
    cache = tmp_path / "cache"
    _make_archive(
        archive,
        [
            ("2024-01-01", "M31 Andromeda Galaxy", "astrophoto"),
            ("2024-01-02", "Sombrero Galaxy", "astrophoto"),
            ("2024-01-03", "Orion Nebula", "astrophoto"),
        ],
    )

    # Cache evaluations for all three. Galaxies have FWHM=3, nebula has FWHM=5.
    base_summary = {
        "image": {},
        "sources": {},
        "star_field": {"median_fwhm_px": 3.0, "median_eccentricity": 0.05, "fwhm_corner_excess": 0.0},
        "noise": {
            "noise_floors": {"L": 0.01},
            "psd_spectral_slope": -0.5,
            "psd_high_band_suppression": 0.1,
            "autocorr_width_px": 1.2,
            "snr_target_median": 20.0,
        },
        "target_freq": {
            "spectral_slope": -2.0,
            "effective_resolution_cycles_per_px": 0.3,
            "n_artifacts": 0,
            "artifact_kinds": [],
        },
        "calibration": {
            "gradient_ratio": 2.0,
            "vignetting_falloff": 0.05,
            "color_balance_magnitude": 0.02,
        },
        "color": {
            "overall_score": 0.7,
            "star_diversity_score": 0.7,
            "background_chroma_distance": 0.02,
        },
    }
    s1 = json.loads(json.dumps(base_summary))
    s1["star_field"]["median_fwhm_px"] = 3.0
    s2 = json.loads(json.dumps(base_summary))
    s2["star_field"]["median_fwhm_px"] = 3.5
    s3 = json.loads(json.dumps(base_summary))
    s3["star_field"]["median_fwhm_px"] = 5.0

    _write_eval_cache(cache, archive / "2024" / "2024-01-01.jpg", s1)
    _write_eval_cache(cache, archive / "2024" / "2024-01-02.jpg", s2)
    _write_eval_cache(cache, archive / "2024" / "2024-01-03.jpg", s3)

    dists = build_distributions(cache, archive)
    assert "galaxy" in dists.by_category
    assert "orion_nebula" in dists.by_category
    assert "global" in dists.by_category

    galaxy = dists.by_category["galaxy"]
    fwhm = galaxy["median_fwhm_px"]
    assert fwhm.n == 2
    assert pytest.approx(fwhm.quantiles["p50"], rel=1e-3) == 3.25

    # Save / load roundtrip
    out_path = tmp_path / "dists.json"
    save_distributions(dists, out_path)
    loaded = load_distributions(out_path)
    assert "galaxy" in loaded.by_category
    assert loaded.by_category["galaxy"]["median_fwhm_px"].n == 2


def test_metric_distribution_percentile_directionality():
    """Higher-is-better and lower-is-better metrics should rank values opposite."""
    from apodornot.archive_pipeline import MetricDistribution

    values = list(np.linspace(0.0, 100.0, 101))  # 0..100
    higher = MetricDistribution("x", True, len(values), values, {})
    lower = MetricDistribution("y", False, len(values), values, {})
    # Median value (50) should rank ~50th percentile in both, but in opposite directions.
    assert pytest.approx(higher.percentile(50), abs=2.0) == 50
    assert pytest.approx(lower.percentile(50), abs=2.0) == 50
    # A high value should rank high under higher_is_better, low under lower_is_better.
    assert higher.percentile(90) > 80
    assert lower.percentile(90) < 20


def test_scoring_metrics_directions_are_explicit():
    """Sanity: every metric in SCORING_METRICS has a direction set."""
    for name, path, hib in SCORING_METRICS:
        assert isinstance(name, str) and name
        assert isinstance(path, str) and "." in path
        assert isinstance(hib, bool)
