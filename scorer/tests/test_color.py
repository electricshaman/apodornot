"""Tests for A6 — color analysis."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from apodornot.color import (
    analyze_color,
    detect_palette,
    evaluate_star_colors,
    measure_background_neutrality,
)
from apodornot.image_chars import SEG_BACKGROUND, SEG_TARGET
from apodornot.star_field import StarFieldResult


APOD_FIXTURE = Path("apod_archive/2024/2024-01-15.jpg")


# ---------------------------------------------------------------------------- #
# Star color accuracy
# ---------------------------------------------------------------------------- #


def test_evaluate_star_colors_high_diversity():
    sf = StarFieldResult()
    rng = np.random.default_rng(0)
    # Generate a population spanning blue-red with realistic spread.
    colors = []
    for _ in range(40):
        t = rng.uniform(0, 1)
        # interpolate from a blue to a red star
        rgb = (0.4 + 0.4 * t, 0.4, 0.8 - 0.4 * t)
        colors.append(rgb)
    sf.star_colors = colors
    acc = evaluate_star_colors(sf)
    assert acc is not None
    assert acc.diversity_score > 0.5


def test_evaluate_star_colors_low_diversity():
    sf = StarFieldResult()
    sf.star_colors = [(0.5, 0.5, 0.5)] * 30  # all neutral
    acc = evaluate_star_colors(sf)
    assert acc is not None
    assert acc.diversity_score < 0.1


def test_evaluate_star_colors_empty():
    sf = StarFieldResult()
    assert evaluate_star_colors(sf) is None


# ---------------------------------------------------------------------------- #
# Background neutrality
# ---------------------------------------------------------------------------- #


def test_measure_background_neutrality_neutral_is_zero():
    color = np.full((64, 64, 3), 0.3, dtype=np.float32)
    seg = np.zeros((64, 64), dtype=np.uint8)
    bn = measure_background_neutrality(color, seg)
    assert bn is not None
    assert bn.chroma_distance < 1e-3


def test_measure_background_neutrality_red_cast_is_nonzero():
    color = np.zeros((64, 64, 3), dtype=np.float32)
    color[..., 0] = 0.6
    color[..., 1] = 0.3
    color[..., 2] = 0.3
    seg = np.zeros((64, 64), dtype=np.uint8)
    bn = measure_background_neutrality(color, seg)
    assert bn is not None
    assert bn.chroma_distance > 0.05


# ---------------------------------------------------------------------------- #
# Palette detection
# ---------------------------------------------------------------------------- #


def test_detect_palette_natural_desaturated():
    """All three channels equal -> 'natural' (desaturated)."""
    h, w = 64, 64
    color = np.zeros((h, w, 3), dtype=np.float32)
    rng = np.random.default_rng(0)
    base = rng.uniform(0.2, 0.8, (h, w)).astype(np.float32)
    color[..., 0] = base
    color[..., 1] = base
    color[..., 2] = base
    seg = np.full((h, w), SEG_TARGET, dtype=np.uint8)
    td = detect_palette(color, seg)
    assert td is not None
    assert td.palette == "natural"


def test_detect_palette_hoo_has_red_and_correlated_gb():
    h, w = 64, 64
    rng = np.random.default_rng(0)
    color = np.zeros((h, w, 3), dtype=np.float32)
    ha = rng.uniform(0.1, 0.7, (h, w)).astype(np.float32)
    oiii = rng.uniform(0.1, 0.4, (h, w)).astype(np.float32)
    color[..., 0] = ha           # R channel = Ha
    color[..., 1] = oiii         # G = OIII
    color[..., 2] = oiii * 0.95  # B ≈ OIII (correlated)
    seg = np.full((h, w), SEG_TARGET, dtype=np.uint8)
    td = detect_palette(color, seg)
    assert td is not None
    assert td.palette in ("hoo", "broadband_rgb")  # HOO is the strict match; broadband acceptable


# ---------------------------------------------------------------------------- #
# Top-level
# ---------------------------------------------------------------------------- #


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_analyze_color_real_image():
    from apodornot.image_chars import characterize_image
    from apodornot.star_field import analyze_star_field

    chars = characterize_image(APOD_FIXTURE)
    sf = analyze_star_field(chars, max_stars=80)
    result = analyze_color(chars, sf)
    assert result.background is not None
    assert result.target_distribution is not None
    assert np.isfinite(result.overall_score)
