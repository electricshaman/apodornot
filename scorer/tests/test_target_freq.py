"""Tests for A4 — frequency-domain analysis of target structure."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from apodornot.image_chars import SEG_TARGET
from apodornot.target_freq import (
    analyze_target_frequency,
    detect_artifacts,
    extract_target_window,
    fit_power_law,
    radial_power_spectrum,
)


APOD_FIXTURE = Path("apod_archive/2024/2024-01-15.jpg")


# ---------------------------------------------------------------------------- #
# Region extraction
# ---------------------------------------------------------------------------- #


def test_extract_target_window_returns_none_when_no_target():
    img = np.zeros((128, 128), dtype=np.float32)
    seg = np.zeros_like(img, dtype=np.uint8)
    assert extract_target_window(img, seg) is None


def test_extract_target_window_finds_blob():
    rng = np.random.default_rng(0)
    img = rng.normal(0, 0.01, (256, 256)).astype(np.float32)
    seg = np.zeros_like(img, dtype=np.uint8)
    seg[60:200, 60:200] = SEG_TARGET
    win = extract_target_window(img, seg)
    assert win is not None
    assert win.ndim == 2


# ---------------------------------------------------------------------------- #
# Radial PSD on synthetic data
# ---------------------------------------------------------------------------- #


def _power_law_image(shape, slope, rng):
    """Generate noise with a 1/f^slope power spectrum (radial)."""
    h, w = shape
    fx = np.fft.fftfreq(w)[None, :]
    fy = np.fft.fftfreq(h)[:, None]
    f = np.sqrt(fx * fx + fy * fy)
    f[0, 0] = 1e-9  # avoid div by 0
    amp = 1.0 / (f ** (slope / 2.0))
    phase = np.exp(2j * np.pi * rng.random((h, w)))
    field = np.fft.ifft2(amp * phase).real
    return field.astype(np.float32)


def test_radial_psd_recovers_power_law_slope():
    rng = np.random.default_rng(0)
    img = _power_law_image((256, 256), slope=2.0, rng=rng)

    seg = np.full(img.shape, SEG_TARGET, dtype=np.uint8)
    win = extract_target_window(img, seg)
    assert win is not None
    freqs, radial = radial_power_spectrum(win)

    slope, intercept, _, _ = fit_power_law(freqs, radial, low_cut=0.02, high_cut=0.2)
    # Power law of f^-slope means PSD ~ 1/f^slope, so log(P) ~ -slope * log(f).
    assert -3.0 < slope < -1.0


def test_radial_psd_white_noise_slope_near_zero():
    rng = np.random.default_rng(0)
    img = rng.normal(0, 1, (256, 256)).astype(np.float32)
    seg = np.full(img.shape, SEG_TARGET, dtype=np.uint8)
    win = extract_target_window(img, seg)
    assert win is not None
    freqs, radial = radial_power_spectrum(win)
    slope, _, _, _ = fit_power_law(freqs, radial, low_cut=0.02, high_cut=0.2)
    assert -1.0 < slope < 1.0


# ---------------------------------------------------------------------------- #
# Artifact detection
# ---------------------------------------------------------------------------- #


def test_detect_oversharpening_when_high_freq_bumped():
    # Build a fake radial PSD that follows a power law with a bump at f=0.2.
    freqs = np.linspace(0.001, 0.45, 150)
    radial = freqs ** -2.0
    # Inject a strong bump: +12 dB above the power-law fit at f~0.2
    bump_idx = (np.abs(freqs - 0.2)).argmin()
    radial[bump_idx - 3 : bump_idx + 3] *= 15.0  # ~12 dB

    artifacts = detect_artifacts(
        freqs, radial, slope=-2.0, intercept=0.0, fwhm_px=2.0, fit_low=0.02, fit_high=0.4
    )
    kinds = [a.kind for a in artifacts]
    assert "oversharpening" in kinds


def test_detect_artifacts_clean_psd_returns_few():
    rng = np.random.default_rng(0)
    freqs = np.linspace(0.001, 0.45, 150)
    radial = freqs ** -2.0 * (1 + 0.05 * rng.standard_normal(150))
    radial = np.maximum(radial, 1e-9)

    artifacts = detect_artifacts(
        freqs, radial, slope=-2.0, intercept=0.0, fwhm_px=3.0, fit_low=0.02, fit_high=0.4
    )
    # Mild fluctuations may produce minor "ringing" peaks, but no oversharpening.
    severities = [a.severity for a in artifacts if a.kind == "oversharpening"]
    assert not severities or max(severities) < 0.3


# ---------------------------------------------------------------------------- #
# End-to-end on a real APOD image
# ---------------------------------------------------------------------------- #


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_analyze_target_frequency_real_image():
    from apodornot.image_chars import characterize_image

    chars = characterize_image(APOD_FIXTURE)
    result = analyze_target_frequency(chars, fwhm_px=4.0)
    # Either the image had a usable target, or it didn't — both are valid outcomes.
    if result.radial_power.size > 0:
        assert np.isfinite(result.spectral_slope)
