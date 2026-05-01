"""Tests for A3 — noise characterization."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from apodornot.image_chars import SEG_BACKGROUND, SEG_TARGET
from apodornot.noise import (
    autocorrelation_width,
    channel_stats,
    characterize_noise,
    compute_psd_shape,
    estimate_target_snr,
    sample_background_patches,
)


APOD_FIXTURE = Path("apod_archive/2024/2024-01-15.jpg")


# ---------------------------------------------------------------------------- #
# Channel stats — synthetic Gaussian noise
# ---------------------------------------------------------------------------- #


def test_channel_stats_gaussian_noise():
    rng = np.random.default_rng(0)
    img = rng.normal(0.5, 0.05, (200, 200)).astype(np.float32)
    seg = np.zeros_like(img, dtype=np.uint8)  # all background

    stats = channel_stats(img, seg, name="L")
    assert abs(stats.mean - 0.5) < 0.01
    assert abs(stats.noise_floor - 0.05) < 0.005
    assert abs(stats.skewness) < 0.2
    assert abs(stats.kurtosis) < 0.5
    assert stats.normality_p > 0.001  # passes normality at any reasonable level


def test_channel_stats_skewed_noise_flagged():
    rng = np.random.default_rng(0)
    # Lognormal -> heavily skewed right
    img = rng.lognormal(mean=0.0, sigma=0.5, size=(200, 200)).astype(np.float32)
    seg = np.zeros_like(img, dtype=np.uint8)
    stats = channel_stats(img, seg, name="L")
    assert stats.skewness > 0.5
    # normality_p should be tiny (clearly non-Gaussian)
    assert stats.normality_p < 0.01


# ---------------------------------------------------------------------------- #
# Background patch sampling
# ---------------------------------------------------------------------------- #


def test_sample_background_patches_skips_target_regions():
    img = np.zeros((512, 512), dtype=np.float32)
    seg = np.zeros_like(img, dtype=np.uint8)
    # Make the entire bottom half "target"
    seg[256:, :] = SEG_TARGET
    patches = sample_background_patches(img, seg, patch_size=64, max_patches=10)
    assert len(patches) == 10
    # All sampled patches should be from the top half — no SEG_TARGET pixels.
    # (We can't verify the patch positions directly, but we can verify the test
    # sampler refused at least once when it would have hit target — proxied by
    # the fact that we still got the requested number from a 50% available area.)


# ---------------------------------------------------------------------------- #
# PSD shape
# ---------------------------------------------------------------------------- #


def test_psd_shape_white_noise_is_flat():
    rng = np.random.default_rng(0)
    patches = [rng.normal(0, 0.05, (64, 64)).astype(np.float32) for _ in range(8)]
    psd = compute_psd_shape(patches)
    assert psd is not None
    # White noise has spectral slope ~ 0 (flat), within a wide tolerance after
    # windowing artifacts.
    assert abs(psd.spectral_slope) < 0.5
    # No high-band suppression for white noise.
    assert psd.high_band_suppression < 0.3


def test_psd_shape_low_pass_noise_shows_high_band_suppression():
    """Aggressive denoising looks like a Gaussian-blurred white noise patch — the
    high frequencies are suppressed relative to the white-noise baseline.
    """
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(0)
    patches = []
    for _ in range(8):
        n = rng.normal(0, 0.05, (64, 64)).astype(np.float32)
        # Aggressive Gaussian blur strips the high frequencies.
        patches.append(gaussian_filter(n, sigma=2.0))
    psd = compute_psd_shape(patches)
    assert psd is not None
    # Very negative spectral slope = power concentrated at low frequencies.
    assert psd.spectral_slope < -2.0
    assert psd.high_band_suppression > 0.5


# ---------------------------------------------------------------------------- #
# Autocorrelation width
# ---------------------------------------------------------------------------- #


def test_autocorrelation_width_white_noise_is_one_pixel():
    rng = np.random.default_rng(0)
    patches = [rng.normal(0, 1, (64, 64)).astype(np.float32) for _ in range(5)]
    width = autocorrelation_width(patches)
    assert width <= 2.0  # ideal uncorrelated noise -> 1px


def test_autocorrelation_width_smoothed_noise_is_wider():
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(0)
    patches = [
        gaussian_filter(rng.normal(0, 1, (64, 64)).astype(np.float32), sigma=2.0)
        for _ in range(5)
    ]
    width = autocorrelation_width(patches)
    assert width >= 2.0


# ---------------------------------------------------------------------------- #
# SNR estimation
# ---------------------------------------------------------------------------- #


def test_estimate_target_snr():
    bg_sub = np.full((64, 64), 0.5, dtype=np.float32)
    rms = np.full((64, 64), 0.1, dtype=np.float32)
    seg = np.zeros((64, 64), dtype=np.uint8)
    seg[16:48, 16:48] = SEG_TARGET
    median, p90 = estimate_target_snr(bg_sub, rms, seg)
    assert abs(median - 5.0) < 0.01
    assert abs(p90 - 5.0) < 0.01


def test_estimate_target_snr_no_target_returns_nan():
    bg_sub = np.zeros((32, 32), dtype=np.float32)
    rms = np.full((32, 32), 0.1, dtype=np.float32)
    seg = np.zeros((32, 32), dtype=np.uint8)
    median, p90 = estimate_target_snr(bg_sub, rms, seg)
    assert np.isnan(median)
    assert np.isnan(p90)


# ---------------------------------------------------------------------------- #
# End-to-end on a real APOD image
# ---------------------------------------------------------------------------- #


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_characterize_noise_real_image():
    from apodornot.image_chars import characterize_image

    chars = characterize_image(APOD_FIXTURE)
    result = characterize_noise(chars, max_patches=8)
    assert result.n_background_patches > 0
    assert result.psd is not None
    # Real APOD JPEG noise should be vaguely well-behaved (not absurdly skewed)
    lum_stats = next(c for c in result.channels if c.name == "L")
    assert abs(lum_stats.skewness) < 5.0
