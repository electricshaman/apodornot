"""Tests for A5 — gradient and calibration assessment."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from apodornot.gradient import (
    assess_calibration,
    detect_vignetting,
    measure_color_balance,
    measure_gradient,
)
from apodornot.image_chars import SEG_BACKGROUND


APOD_FIXTURE = Path("apod_archive/2024/2024-01-15.jpg")


# ---------------------------------------------------------------------------- #
# Gradient
# ---------------------------------------------------------------------------- #


def test_measure_gradient_flat_background_low_ratio():
    bg = np.full((128, 128), 0.2, dtype=np.float32)
    rms = np.full_like(bg, 0.01)
    g = measure_gradient(bg, rms)
    assert g.peak_to_peak < 1e-3
    assert g.gradient_ratio < 1.0


def test_measure_gradient_strong_linear_gradient_high_ratio():
    h, w = 128, 128
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    bg = (0.2 + 0.05 * (xx / w)).astype(np.float32)  # linear ramp left -> right
    rms = np.full_like(bg, 0.005)
    g = measure_gradient(bg, rms)
    assert g.peak_to_peak > 0.04
    assert g.gradient_ratio > 5.0
    # Gradient should point in the +x direction (~ 0 deg).
    assert g.direction_deg < 30 or g.direction_deg > 330


# ---------------------------------------------------------------------------- #
# Vignetting
# ---------------------------------------------------------------------------- #


def test_detect_vignetting_recovers_radial_falloff():
    h = w = 256
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / np.hypot(cx, cy)
    bg = (1.0 - 0.2 * r * r).astype(np.float32)
    v = detect_vignetting(bg)
    assert v.fit_succeeded
    assert 0.10 < v.center_to_corner_falloff < 0.30  # we injected ~20%


def test_detect_vignetting_flat_returns_zero_falloff():
    bg = np.full((128, 128), 0.5, dtype=np.float32)
    v = detect_vignetting(bg)
    assert v.fit_succeeded
    assert abs(v.center_to_corner_falloff) < 0.05


# ---------------------------------------------------------------------------- #
# Color balance
# ---------------------------------------------------------------------------- #


def test_measure_color_balance_neutral():
    color = np.full((64, 64, 3), 0.3, dtype=np.float32)
    seg = np.zeros((64, 64), dtype=np.uint8)  # all background
    cb = measure_color_balance(color, seg)
    assert cb is not None
    assert cb.deviation_magnitude < 1e-3


def test_measure_color_balance_cast():
    color = np.zeros((64, 64, 3), dtype=np.float32)
    color[..., 0] = 0.4  # heavy red cast
    color[..., 1] = 0.3
    color[..., 2] = 0.3
    seg = np.zeros((64, 64), dtype=np.uint8)
    cb = measure_color_balance(color, seg)
    assert cb is not None
    assert cb.bg_means["R"] > cb.bg_means["G"]
    assert cb.deviation_magnitude > 0.1


# ---------------------------------------------------------------------------- #
# End-to-end on a real APOD image
# ---------------------------------------------------------------------------- #


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_assess_calibration_real_image():
    from apodornot.image_chars import characterize_image

    chars = characterize_image(APOD_FIXTURE)
    result = assess_calibration(chars)
    assert np.isfinite(result.gradient.peak_to_peak)
    assert result.color_balance is not None
