"""Tests for A1 — image characterization and segmentation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from apodornot.image_chars import (
    SEG_BACKGROUND,
    SEG_SOURCE,
    SEG_TARGET,
    build_segmentation_mask,
    characterize_image,
    classify_sources,
    estimate_background,
    extract_sources,
    load_and_normalize,
)


APOD_FIXTURE = Path("apod_archive/2024/2024-01-15.jpg")


# ---------------------------------------------------------------------------- #
# load_and_normalize
# ---------------------------------------------------------------------------- #


def test_load_jpeg_normalizes_to_unit(tmp_path):
    from PIL import Image

    arr = np.zeros((40, 60, 3), dtype=np.uint8)
    arr[..., 0] = 255  # red channel saturated
    img_path = tmp_path / "img.jpg"
    Image.fromarray(arr).save(img_path)

    lum, color, meta = load_and_normalize(img_path)
    assert lum.dtype == np.float32
    assert color is not None
    assert color.shape == (40, 60, 3)
    assert meta.bit_depth == 8
    assert meta.is_color is True
    # Red channel is luminance-weighted at 0.2126
    assert lum.max() <= 1.0 and lum.min() >= 0.0


def test_load_mono_png(tmp_path):
    from PIL import Image

    arr = (np.linspace(0, 65535, 60 * 40).reshape(40, 60)).astype(np.uint16)
    img_path = tmp_path / "img.png"
    Image.fromarray(arr, mode="I;16").save(img_path)

    lum, color, meta = load_and_normalize(img_path)
    assert color is None
    assert meta.is_color is False
    assert meta.bit_depth in (16, 32)  # PIL may return 32-bit
    assert lum.max() <= 1.0


def test_load_fits(tmp_path):
    from astropy.io import fits

    data = (np.random.RandomState(0).poisson(50, size=(64, 64))).astype(np.float32)
    p = tmp_path / "x.fits"
    fits.PrimaryHDU(data, header=fits.Header({"EXPTIME": 60.0, "GAIN": 1.0})).writeto(p)
    lum, color, meta = load_and_normalize(p)
    assert color is None
    assert lum.shape == (64, 64)
    assert meta.fits_header.get("EXPTIME") == 60.0
    assert meta.bit_depth == 32


# ---------------------------------------------------------------------------- #
# Background + source detection on synthetic data
# ---------------------------------------------------------------------------- #


def test_estimate_background_recovers_constant_floor(synthetic_starfield):
    img, _ = synthetic_starfield
    bg, rms = estimate_background(img, box_size=32)
    assert bg.shape == img.shape
    assert rms.shape == img.shape
    # We added a 0.05 background; SEP should land within ~ a few percent.
    assert 0.03 < float(np.median(bg)) < 0.07


def test_extract_sources_finds_synthetic_stars(synthetic_starfield):
    img, stars = synthetic_starfield
    bg, rms = estimate_background(img, box_size=32)
    objs = extract_sources((img - bg).astype(np.float32), rms)
    # We injected 40 stars; allow some overlap-driven loss but expect most.
    assert objs.shape[0] >= len(stars) * 0.7


def test_classify_sources_partitions_population():
    # Build a fake structured array with mixed sizes.
    dtype = [("x", "f4"), ("y", "f4"), ("a", "f4"), ("b", "f4"), ("flux", "f4")]
    rows = []
    for _ in range(30):
        rows.append((0, 0, 1.5, 1.4, 100))   # star-like (size ~ 1.45)
    for _ in range(5):
        rows.append((0, 0, 6.0, 5.5, 1000))  # extended (~5.7)
    for _ in range(3):
        rows.append((0, 0, 0.4, 0.4, 5))     # artifact-tight (~0.4)
    cat = np.array(rows, dtype=dtype)

    classified = classify_sources(cat)
    assert len(classified.stars) >= 25
    assert len(classified.extended) >= 4
    assert len(classified.artifacts) >= 2
    assert 1.0 < classified.median_psf_size < 2.0


def test_classify_sources_handles_empty():
    dtype = [("x", "f4"), ("y", "f4"), ("a", "f4"), ("b", "f4"), ("flux", "f4")]
    cat = np.array([], dtype=dtype)
    classified = classify_sources(cat)
    assert classified.total_detected() == 0
    assert len(classified.stars) == 0


# ---------------------------------------------------------------------------- #
# Segmentation mask
# ---------------------------------------------------------------------------- #


def test_build_segmentation_mask_marks_diffuse_signal_and_sources():
    rng = np.random.default_rng(0)
    h = w = 128
    bg_sub = rng.normal(0, 0.01, (h, w)).astype(np.float32)
    rms = np.full_like(bg_sub, 0.01)
    # A diffuse blob in the lower-right quadrant
    yy, xx = np.mgrid[0:h, 0:w]
    blob = 0.05 * np.exp(-((xx - 96) ** 2 + (yy - 96) ** 2) / (2 * 12.0**2)).astype(np.float32)
    bg_sub = bg_sub + blob

    # Plus one bright "source" at (32, 32)
    dtype = [("x", "f4"), ("y", "f4"), ("a", "f4"), ("b", "f4"), ("flux", "f4")]
    cat = np.array([(32, 32, 2.0, 2.0, 10.0)], dtype=dtype)
    bg_sub[28:36, 28:36] += 0.5

    from apodornot.image_chars import SourceCatalog

    sources = SourceCatalog(stars=cat, extended=cat[:0], artifacts=cat[:0], raw=cat)
    mask = build_segmentation_mask(bg_sub, rms, sources)

    assert mask.shape == (h, w)
    # Source pixel near (32,32) should be flagged as SEG_SOURCE.
    assert mask[32, 32] == SEG_SOURCE
    # Background corner should be SEG_BACKGROUND.
    assert mask[0, 0] == SEG_BACKGROUND
    # The blob center should be SEG_TARGET (or SEG_SOURCE if the source mask
    # happens to land there — but our source is far away).
    assert mask[96, 96] == SEG_TARGET


# ---------------------------------------------------------------------------- #
# End-to-end on a real APOD image (skipped if not downloaded)
# ---------------------------------------------------------------------------- #


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_characterize_real_apod_image():
    result = characterize_image(APOD_FIXTURE)
    assert result.luminance.ndim == 2
    assert result.background.shape == result.luminance.shape
    assert result.segmentation.shape == result.luminance.shape
    assert result.sources.total_detected() > 50
    assert result.metadata.is_color  # APOD JPEGs are color
