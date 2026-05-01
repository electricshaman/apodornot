"""Shared pytest fixtures for apodornot."""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(0xA90D)


@pytest.fixture
def synthetic_starfield(rng):
    """A 256x256 luminance image with synthetic background + Gaussian stars + noise.

    Returns ``(image, stars)`` where ``stars`` is a list of ``(x, y, flux, fwhm)`` tuples.
    """
    h = w = 256
    img = np.full((h, w), 0.05, dtype=np.float32)  # mild background
    stars: list[tuple[float, float, float, float]] = []

    # Sprinkle stars across the field
    n_stars = 40
    xs = rng.uniform(15, w - 15, n_stars)
    ys = rng.uniform(15, h - 15, n_stars)
    fluxes = rng.uniform(50.0, 500.0, n_stars)
    fwhms = rng.uniform(2.5, 3.5, n_stars)

    yy, xx = np.mgrid[0:h, 0:w]
    for x, y, flux, fwhm in zip(xs, ys, fluxes, fwhms):
        sigma = fwhm / 2.3548
        img += flux * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2)).astype(
            np.float32
        ) / (2 * np.pi * sigma**2)
        stars.append((float(x), float(y), float(flux), float(fwhm)))

    # Gaussian noise floor
    img += rng.normal(0.0, 0.01, img.shape).astype(np.float32)
    img = np.clip(img, 0.0, None)
    return img, stars
