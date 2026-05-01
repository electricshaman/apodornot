"""Tests for input-domain classification + score-time domain-mismatch warnings."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from apodornot.image_chars import (
    _classify_input_domain,
    _file_format_from_path,
    load_and_normalize,
)
from apodornot.scoring import ScoreCard, _domain_warnings


# ---------------------------------------------------------------------------- #
# File-format detection
# ---------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,expected",
    [
        ("foo.jpg", "jpeg"),
        ("foo.JPEG", "jpeg"),
        ("foo.png", "png"),
        ("foo.tif", "tiff"),
        ("foo.tiff", "tiff"),
        ("foo.fits", "fits"),
        ("foo.fit", "fits"),
        ("foo.heic", "unknown"),
    ],
)
def test_file_format_from_path(name, expected):
    assert _file_format_from_path(Path(name)) == expected


# ---------------------------------------------------------------------------- #
# Domain classifier
# ---------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fmt,bd,expected",
    [
        ("jpeg", 8, "display"),
        ("png", 8, "display"),
        ("png", 16, "linear"),
        ("tiff", 16, "linear"),
        ("tiff", 8, "display"),
        ("fits", 32, "linear"),
        ("fits", 16, "linear"),
        ("unknown", 8, "unknown"),
    ],
)
def test_classify_input_domain(fmt, bd, expected):
    assert _classify_input_domain(fmt, bd) == expected


# ---------------------------------------------------------------------------- #
# load_and_normalize plumbs domain through to metadata
# ---------------------------------------------------------------------------- #


def test_jpeg_is_display_domain(tmp_path):
    arr = np.full((40, 60, 3), 128, dtype=np.uint8)
    p = tmp_path / "x.jpg"
    Image.fromarray(arr).save(p)
    _, _, meta = load_and_normalize(p)
    assert meta.file_format == "jpeg"
    assert meta.input_domain == "display"


def test_16bit_png_is_linear_domain(tmp_path):
    arr = (np.linspace(0, 65535, 40 * 60).reshape(40, 60)).astype(np.uint16)
    p = tmp_path / "x.png"
    Image.fromarray(arr, mode="I;16").save(p)
    _, _, meta = load_and_normalize(p)
    assert meta.file_format == "png"
    assert meta.input_domain == "linear"


def test_fits_is_linear_domain(tmp_path):
    from astropy.io import fits

    data = np.zeros((40, 40), dtype=np.float32)
    p = tmp_path / "x.fits"
    fits.PrimaryHDU(data).writeto(p)
    _, _, meta = load_and_normalize(p)
    assert meta.file_format == "fits"
    assert meta.input_domain == "linear"


# ---------------------------------------------------------------------------- #
# Score-time warnings
# ---------------------------------------------------------------------------- #


def test_domain_warnings_linear_input_warns():
    warnings = _domain_warnings("linear", reference_domain="display")
    assert len(warnings) == 1
    assert "linear/master data" in warnings[0]
    assert "JPEG" in warnings[0]


def test_domain_warnings_display_input_silent():
    warnings = _domain_warnings("display", reference_domain="display")
    assert warnings == []


def test_domain_warnings_unknown_input_advises_caution():
    warnings = _domain_warnings("unknown", reference_domain="display")
    assert len(warnings) == 1
    assert "approximate" in warnings[0]


# ---------------------------------------------------------------------------- #
# End-to-end on a real FITS file
# ---------------------------------------------------------------------------- #


def test_score_evaluation_on_fits_emits_warning(tmp_path):
    """A linear-domain submission should produce a domain-mismatch warning in the scorecard."""
    from astropy.io import fits

    from apodornot.pipeline import evaluate_image
    from apodornot.scoring import score_evaluation

    rng = np.random.default_rng(0)
    h = w = 256
    yy, xx = np.mgrid[0:h, 0:w]
    img = rng.normal(50.0, 5.0, (h, w)).astype(np.float32)
    # Add a few synthetic stars so source detection has things to find.
    for cx, cy in [(60, 60), (180, 60), (60, 180), (180, 180), (128, 128)]:
        sigma = 2.5
        img += 800 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma ** 2)) / (
            2 * np.pi * sigma ** 2
        )

    p = tmp_path / "synthetic.fits"
    fits.PrimaryHDU(img).writeto(p)

    eval_result = evaluate_image(p)
    assert eval_result.image_chars.metadata.input_domain == "linear"

    sc = score_evaluation(eval_result)
    assert sc.warnings, "expected a domain-mismatch warning for FITS input"
    assert any("linear/master data" in w for w in sc.warnings)
    assert "Warnings" in sc.text_report()
