"""Tests for A2 — star field analysis."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from apodornot.image_chars import (
    SourceCatalog,
    build_segmentation_mask,
    classify_sources,
    estimate_background,
    extract_sources,
)
from apodornot.star_field import (
    _fit_one_star,
    analyze_field_variation,
    analyze_star_field,
    extract_star_colors,
    fit_stars,
    moffat_2d,
)


APOD_FIXTURE = Path("apod_archive/2024/2024-01-15.jpg")


# ---------------------------------------------------------------------------- #
# Moffat fit — single star, ground truth FWHM
# ---------------------------------------------------------------------------- #


def _make_synthetic_star(
    fwhm: float, ratio: float = 1.0, theta: float = 0.0, *, size: int = 21, beta: float = 3.5
):
    yy, xx = np.mgrid[0:size, 0:size]
    alpha = fwhm / (2.0 * math.sqrt(2.0 ** (1.0 / beta) - 1.0))
    img = moffat_2d(
        (xx.ravel(), yy.ravel()),
        amp=100.0,
        x0=size / 2.0,
        y0=size / 2.0,
        alpha=alpha,
        beta=beta,
        floor=0.0,
        ratio=ratio,
        theta=theta,
    ).reshape(size, size)
    return img.astype(np.float32)


def test_fit_one_star_recovers_fwhm():
    img = _make_synthetic_star(fwhm=3.0)
    rng = np.random.default_rng(0)
    img = img + rng.normal(0, 0.5, img.shape).astype(np.float32)
    m = _fit_one_star(img, cx=100.0, cy=200.0, initial_alpha=2.0)
    assert m is not None
    assert abs(m.fwhm - 3.0) < 0.3
    assert m.eccentricity < 0.2  # round star (noisy fit, allow small distortion)


def test_fit_one_star_recovers_eccentricity():
    img = _make_synthetic_star(fwhm=3.0, ratio=0.5, theta=0.0)
    m = _fit_one_star(img, cx=0.0, cy=0.0, initial_alpha=2.0)
    assert m is not None
    # b/a = 0.5 -> ecc = sqrt(1 - 0.25) = ~0.866
    assert m.eccentricity > 0.6
    assert abs(m.pa_deg - 0.0) < 15 or abs(m.pa_deg - 180.0) < 15  # axis-aligned


# ---------------------------------------------------------------------------- #
# Field variation classification
# ---------------------------------------------------------------------------- #


def _make_measurements(specs):
    """specs: list of (x, y, fwhm, ecc, pa_deg)."""
    from apodornot.star_field import StarMeasurement

    return [
        StarMeasurement(
            x=x, y=y, flux=1.0, fwhm=fwhm, fwhm_x=fwhm, fwhm_y=fwhm * (1 - ecc),
            eccentricity=ecc, pa_deg=pa, beta=3.5, chi2_red=1.0, saturated=False,
        )
        for (x, y, fwhm, ecc, pa) in specs
    ]


def test_field_variation_clean_round_stars():
    specs = [(50 + i * 5, 50 + j * 5, 3.0, 0.05, 0.0) for i in range(8) for j in range(8)]
    fv = analyze_field_variation(_make_measurements(specs), (256, 256))
    assert fv.eccentricity_pattern == "clean"


def test_field_variation_uniform_drift_pattern():
    # All stars elongated in the same direction (45°)
    specs = [(50 + i * 20, 50 + j * 20, 3.0, 0.4, 45.0) for i in range(8) for j in range(8)]
    fv = analyze_field_variation(_make_measurements(specs), (256, 256))
    assert fv.eccentricity_pattern == "uniform_drift"
    assert fv.pa_concentration > 0.6


def test_field_variation_radial_pattern():
    # Stars elongated along the line from image center
    cx, cy = 128, 128
    specs = []
    for i in range(8):
        for j in range(8):
            x = 20 + i * 28
            y = 20 + j * 28
            pa = math.degrees(math.atan2(y - cy, x - cx)) % 180
            specs.append((x, y, 3.0, 0.4, pa))
    fv = analyze_field_variation(_make_measurements(specs), (256, 256))
    assert fv.eccentricity_pattern == "radial"
    assert fv.radial_alignment_score > 0.4


def test_field_variation_corner_excess():
    # Inner stars sharp, corner stars bloated -> coma/curvature signature.
    specs = []
    cx, cy = 128, 128
    for i in range(8):
        for j in range(8):
            x = 20 + i * 28
            y = 20 + j * 28
            r = math.hypot(x - cx, y - cy)
            fwhm = 3.0 + 2.0 * (r / 180.0)
            specs.append((x, y, fwhm, 0.05, 0.0))
    fv = analyze_field_variation(_make_measurements(specs), (256, 256))
    assert fv.fwhm_corner_excess > 0.1


# ---------------------------------------------------------------------------- #
# fit_stars on the synthetic star field
# ---------------------------------------------------------------------------- #


def test_fit_stars_on_synthetic_field(synthetic_starfield):
    img, ground = synthetic_starfield
    bg, rms = estimate_background(img, box_size=32)
    bg_sub = (img - bg).astype(np.float32)
    raw = extract_sources(bg_sub, rms)
    classified = classify_sources(raw)
    measurements = fit_stars(bg_sub, classified, max_stars=80, cutout_half_size=8)
    assert len(measurements) >= 15
    # Median FWHM should be in a sensible ballpark — the synthetic stars use
    # FWHMs in 2.5..3.5 (Gaussian); fit as Moffat will land close.
    fwhms = [m.fwhm for m in measurements]
    assert 2.0 < float(np.median(fwhms)) < 5.0


# ---------------------------------------------------------------------------- #
# Star colors
# ---------------------------------------------------------------------------- #


def test_extract_star_colors_returns_color_ratios():
    # Build a small color image with a few non-saturating, blueish stars.
    from apodornot.star_field import StarMeasurement

    h = w = 64
    color_img = np.zeros((h, w, 3), dtype=np.float32)

    # Three stars, each peak ~0.6 in blue, 0.45 in green, 0.3 in red
    for cx, cy in [(16, 16), (32, 32), (48, 48)]:
        yy, xx = np.mgrid[0:h, 0:w]
        peak = 0.6 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 1.5 ** 2)).astype(np.float32)
        color_img[..., 2] += peak
        color_img[..., 1] += peak * 0.75
        color_img[..., 0] += peak * 0.5
    color_img = np.clip(color_img, 0, 0.95)

    measurements = [
        StarMeasurement(
            x=cx, y=cy, flux=1.0, fwhm=3.0, fwhm_x=3.0, fwhm_y=3.0,
            eccentricity=0.05, pa_deg=0.0, beta=3.5, chi2_red=1.0, saturated=False,
        )
        for (cx, cy) in [(16, 16), (32, 32), (48, 48)]
    ]
    colors = extract_star_colors(color_img, measurements)
    assert len(colors) == 3
    rgbs = np.array(colors)
    assert float(np.mean(rgbs[:, 2])) > float(np.mean(rgbs[:, 0]))  # blue > red


# ---------------------------------------------------------------------------- #
# End-to-end on a real APOD image
# ---------------------------------------------------------------------------- #


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_analyze_star_field_end_to_end():
    from apodornot.image_chars import characterize_image

    chars = characterize_image(APOD_FIXTURE)
    result = analyze_star_field(chars, max_stars=80)
    assert len(result.measurements) >= 20
    assert result.field_variation is not None
    assert math.isfinite(result.field_variation.median_fwhm)
