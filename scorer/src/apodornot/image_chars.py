"""A1 — Image characterization and segmentation.

Loads an astrophoto (FITS / TIFF / PNG / JPEG), normalizes to float32 in [0, 1],
estimates a sky background and noise (RMS) map via SEP, detects sources, classifies
them as stars / extended / artifacts, and produces a segmentation mask labeling
each pixel as background, target, or source.

The output dataclass ``ImageCharacterization`` is the contract that A2-A6 read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .logging import get_logger

log = get_logger("apodornot.image_chars")


# Pixel labels in the segmentation mask. Bit-flag-friendly so a pixel can be
# both "source" and "target" (a bright star embedded in nebulosity).
SEG_BACKGROUND = 0
SEG_TARGET = 1
SEG_SOURCE = 2


# ---------------------------------------------------------------------------- #
# Data model
# ---------------------------------------------------------------------------- #


@dataclass
class SourceCatalog:
    """Catalog of detected sources, partitioned by class.

    Each ``ndarray`` is a structured record array with at least the SEP fields
    (``x``, ``y``, ``flux``, ``a``, ``b``, ``theta``, ``cflux``, ``cpeak``).
    """

    stars: np.ndarray
    extended: np.ndarray
    artifacts: np.ndarray
    raw: np.ndarray = field(repr=False)
    median_psf_size: float = float("nan")  # median sqrt(a*b) of star class

    def total_detected(self) -> int:
        return int(self.raw.shape[0])


@dataclass
class ImageMetadata:
    path: str
    bit_depth: int
    width: int
    height: int
    is_color: bool
    dynamic_range_used: float       # fraction of original range with at least 1 pixel
    clipped_low_fraction: float     # fraction of pixels exactly at the low limit
    clipped_high_fraction: float    # fraction of pixels exactly at the high limit
    histogram_low_percentile: float
    histogram_high_percentile: float
    file_format: str = "unknown"    # "jpeg" / "png" / "tiff" / "fits"
    input_domain: str = "unknown"   # "display" (8-bit jpeg/png) | "linear" (FITS, 16-bit master)
    fits_header: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageCharacterization:
    luminance: np.ndarray            # 2D float32 in [0, 1]
    color: np.ndarray | None         # 3D float32 (H, W, 3) in [0, 1] or None
    background: np.ndarray           # 2D float32 — estimated sky level per pixel
    rms: np.ndarray                  # 2D float32 — local RMS per pixel
    background_subtracted: np.ndarray
    sources: SourceCatalog
    segmentation: np.ndarray         # 2D uint8, values from SEG_*
    metadata: ImageMetadata

    @property
    def height(self) -> int:
        return self.luminance.shape[0]

    @property
    def width(self) -> int:
        return self.luminance.shape[1]


# ---------------------------------------------------------------------------- #
# A1.1 — Load and normalize
# ---------------------------------------------------------------------------- #


_FITS_SUFFIXES = (".fits", ".fit", ".fts", ".fz")


def _detect_bit_depth(arr: np.ndarray) -> int:
    """Best-effort guess at the source bit depth based on dtype + value range."""
    if arr.dtype == np.uint8:
        return 8
    if arr.dtype == np.uint16:
        return 16
    if arr.dtype in (np.float32, np.float64):
        return 32
    if arr.dtype == np.int16:
        return 16
    if arr.dtype == np.int32:
        return 32
    return int(np.dtype(arr.dtype).itemsize * 8)


def _luminance_from_color(color: np.ndarray) -> np.ndarray:
    """Rec. 709 luminance — preserves perceptual brightness."""
    return (0.2126 * color[..., 0] + 0.7152 * color[..., 1] + 0.0722 * color[..., 2]).astype(
        np.float32
    )


def _normalize_to_unit(arr: np.ndarray, bit_depth: int) -> np.ndarray:
    """Return a float32 copy normalized so the original full-scale value -> 1.0."""
    arr = np.asarray(arr)
    if arr.dtype == np.uint8:
        return (arr.astype(np.float32) / 255.0)
    if arr.dtype == np.uint16:
        return (arr.astype(np.float32) / 65535.0)
    if arr.dtype.kind == "f":
        f = arr.astype(np.float32)
        # Auto-stretch FITS / float images that aren't already in [0,1].
        finite_max = float(np.nanmax(f)) if np.any(np.isfinite(f)) else 1.0
        finite_min = float(np.nanmin(f)) if np.any(np.isfinite(f)) else 0.0
        if finite_max <= 1.0 and finite_min >= 0.0:
            return f
        rng = max(finite_max - finite_min, 1e-12)
        return ((f - finite_min) / rng).astype(np.float32)
    if arr.dtype == np.int16:
        return ((arr.astype(np.float32) - np.iinfo(np.int16).min) / 65535.0).astype(np.float32)
    if arr.dtype == np.int32:
        f = arr.astype(np.float64)
        rng = max(float(f.max()) - float(f.min()), 1e-12)
        return ((f - f.min()) / rng).astype(np.float32)
    # Fallback
    f = arr.astype(np.float32)
    rng = max(float(f.max()) - float(f.min()), 1e-12)
    return ((f - f.min()) / rng).astype(np.float32)


def _load_fits(path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    from astropy.io import fits

    with fits.open(path, memmap=False) as hdul:
        for hdu in hdul:
            if hdu.data is None:
                continue
            data = np.array(hdu.data)
            header = {k: hdu.header.get(k) for k in hdu.header.keys() if k}
            return data, header
    raise ValueError(f"No image data in FITS file: {path}")


def _load_raster(path: Path) -> np.ndarray:
    """Load TIFF/PNG/JPEG/GIF via skimage, falling back to PIL.

    Animated GIFs (which a handful of pre-2000 APOD entries use) load as a
    multi-frame array of shape (n_frames, H, W[, 3]); take the first frame so
    downstream shape-unpacking works.
    """
    try:
        from skimage import io as skio

        arr = skio.imread(path)
    except Exception:
        from PIL import Image

        # PIL animated GIF iteration: pick frame 0.
        with Image.open(path) as img:
            img.seek(0)
            arr = np.array(img.convert("RGB"))

    # Multi-frame GIFs come back as (n_frames, H, W) or (n_frames, H, W, 3).
    # Collapse to a single frame so the rest of the pipeline sees its expected
    # 2D / 3D shape.
    if arr.ndim == 4:
        arr = arr[0]
    elif arr.ndim == 3 and arr.shape[-1] not in (1, 3, 4):
        # Heuristic: if the last axis isn't a channel count, it's a frame axis.
        arr = arr[0]
    return arr


def load_and_normalize(path: str | Path) -> tuple[np.ndarray, np.ndarray | None, ImageMetadata]:
    """Load an astrophoto, normalize to float32 in [0,1], split luminance + color.

    Returns ``(luminance, color, metadata)``. ``color`` is None for monochrome.
    """
    path = Path(path)
    is_fits = path.suffix.lower() in _FITS_SUFFIXES
    fits_header: dict[str, Any] = {}
    if is_fits:
        raw, fits_header = _load_fits(path)
    else:
        raw = _load_raster(path)

    bit_depth = _detect_bit_depth(raw)

    # Strip alpha channel if present.
    if raw.ndim == 3 and raw.shape[-1] == 4:
        raw = raw[..., :3]

    is_color = raw.ndim == 3 and raw.shape[-1] >= 3

    if is_color:
        color = _normalize_to_unit(raw, bit_depth)
        luminance = _luminance_from_color(color)
    else:
        if raw.ndim == 3:
            raw = raw[..., 0]  # treat single-plane "color" as mono
        luminance = _normalize_to_unit(raw, bit_depth)
        color = None

    h, w = luminance.shape
    p_low, p_high = float(np.percentile(luminance, 0.1)), float(np.percentile(luminance, 99.9))
    clipped_low = float(np.mean(luminance <= p_low * 1.0001))
    clipped_high = float(np.mean(luminance >= p_high * 0.9999))
    dyn_range = float(p_high - p_low)

    file_format = _file_format_from_path(path)
    input_domain = _classify_input_domain(file_format, bit_depth)

    metadata = ImageMetadata(
        path=str(path),
        bit_depth=bit_depth,
        width=int(w),
        height=int(h),
        is_color=bool(is_color),
        dynamic_range_used=dyn_range,
        clipped_low_fraction=clipped_low,
        clipped_high_fraction=clipped_high,
        histogram_low_percentile=p_low,
        histogram_high_percentile=p_high,
        file_format=file_format,
        input_domain=input_domain,
        fits_header=fits_header,
    )
    return luminance, color, metadata


def _file_format_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _FITS_SUFFIXES:
        return "fits"
    if suffix in (".jpg", ".jpeg"):
        return "jpeg"
    if suffix == ".png":
        return "png"
    if suffix in (".tif", ".tiff"):
        return "tiff"
    return "unknown"


def _classify_input_domain(file_format: str, bit_depth: int) -> str:
    """Classify whether the input is 'display' (matches APOD JPEG reference) or
    'linear' (FITS / 16-bit master data, much higher quality and *not* what the
    reference distributions reflect).
    """
    if file_format == "fits":
        return "linear"
    if file_format == "jpeg":
        return "display"
    if file_format in ("png", "tiff"):
        return "linear" if bit_depth >= 16 else "display"
    return "unknown"


# ---------------------------------------------------------------------------- #
# A1.3 — Background modeling
# ---------------------------------------------------------------------------- #


def estimate_background(
    luminance: np.ndarray,
    *,
    box_size: int = 64,
    filter_size: int = 3,
) -> tuple[np.ndarray, np.ndarray]:
    """Mesh-based background + RMS estimation via SEP.

    Returns ``(background_map, rms_map)`` — both same shape as ``luminance``.
    """
    import sep

    # SEP expects native byte order, contiguous, float32 (or float64).
    data = np.ascontiguousarray(luminance, dtype=np.float32)
    if data.dtype.byteorder not in ("=", "|"):
        data = data.byteswap().view(data.dtype.newbyteorder("="))

    bkg = sep.Background(data, bw=box_size, bh=box_size, fw=filter_size, fh=filter_size)
    return bkg.back().astype(np.float32), bkg.rms().astype(np.float32)


# ---------------------------------------------------------------------------- #
# A1.4 — Source detection
# ---------------------------------------------------------------------------- #


def extract_sources(
    background_subtracted: np.ndarray,
    rms: np.ndarray,
    *,
    threshold_sigma: float = 5.0,
    min_area: int = 5,
) -> np.ndarray:
    """Run SEP source extraction at ``threshold_sigma`` over the local RMS.

    Retries at a higher threshold if SEP's internal pixel stack overflows
    (which happens on highly nebulous frames where huge connected regions
    exceed the default 300k pixel buffer).

    Returns SEP's structured catalog ndarray.
    """
    import sep

    # Bump SEP's internal limits once. Defaults (300k pixstack, 1k sub-objects)
    # overflow on highly nebulous or star-cluster frames; 5M and 100k are
    # enough headroom for full-resolution APOD images.
    try:
        sep.set_extract_pixstack(5_000_000)
    except Exception:
        pass
    try:
        sep.set_sub_object_limit(100_000)
    except Exception:
        pass

    data = np.ascontiguousarray(background_subtracted, dtype=np.float32)
    err = np.ascontiguousarray(rms, dtype=np.float32)

    for thresh in (threshold_sigma, threshold_sigma * 2.0, threshold_sigma * 4.0, threshold_sigma * 8.0):
        try:
            return sep.extract(data, thresh, err=err, minarea=min_area)
        except Exception as exc:
            msg = str(exc)
            if (
                "pixel buffer full" in msg
                or "pixstack" in msg
                or "deblending overflow" in msg
                or "sub-objects" in msg
            ):
                log.warning(
                    "SEP overflow at %.1f sigma (%s); retrying at %.1f sigma",
                    thresh, msg.split(".")[0], thresh * 2.0,
                )
                continue
            raise
    # Last resort: raise so the caller can mark this image as failed.
    raise RuntimeError("SEP source extraction failed at every retry threshold")


# ---------------------------------------------------------------------------- #
# A1.5 — Source classification
# ---------------------------------------------------------------------------- #


def classify_sources(raw_catalog: np.ndarray) -> SourceCatalog:
    """Split the raw SEP catalog into stars / extended / artifacts.

    Stars cluster tightly in size-space. We define "size" as ``sqrt(a*b)`` (an
    isotropic equivalent radius), find the modal star size from the lower half
    of the size distribution (which avoids being biased by big extended objects),
    then partition by deviation from that modal size.
    """
    if raw_catalog.shape[0] == 0:
        empty = raw_catalog
        return SourceCatalog(stars=empty, extended=empty, artifacts=empty, raw=empty)

    a = raw_catalog["a"].astype(np.float32)
    b = raw_catalog["b"].astype(np.float32)
    size = np.sqrt(a * b)

    # Use a robust estimate of the typical star size from the small-source half.
    finite = size[np.isfinite(size) & (size > 0)]
    if finite.size == 0:
        return SourceCatalog(
            stars=raw_catalog[:0], extended=raw_catalog[:0], artifacts=raw_catalog, raw=raw_catalog
        )
    lower_half = finite[finite <= np.median(finite)]
    if lower_half.size < 5:
        lower_half = finite
    median_psf = float(np.median(lower_half))

    # Tolerances: stars within 0.5x..2.0x median, smaller = artifact, larger = extended.
    is_star = (size >= 0.5 * median_psf) & (size <= 2.0 * median_psf)
    is_artifact = size < 0.5 * median_psf
    is_extended = size > 2.0 * median_psf

    return SourceCatalog(
        stars=raw_catalog[is_star],
        extended=raw_catalog[is_extended],
        artifacts=raw_catalog[is_artifact],
        raw=raw_catalog,
        median_psf_size=median_psf,
    )


# ---------------------------------------------------------------------------- #
# A1.6 — Target segmentation
# ---------------------------------------------------------------------------- #


def build_segmentation_mask(
    background_subtracted: np.ndarray,
    rms: np.ndarray,
    sources: SourceCatalog,
    *,
    smoothing_sigmas: tuple[float, ...] | None = None,
    target_threshold_sigma: float = 3.0,
) -> np.ndarray:
    """Build a uint8 mask of background / target / source.

    Multi-scale smoothing detects diffuse target signal above the noise floor.
    Noise at each smoothing scale is estimated empirically from the sigma-clipped
    MAD of the smoothed image (more robust than the analytical Gaussian-filter
    noise-reduction formula, which underestimates effective noise once any
    diffuse signal is present). Source pixels (from SEP) are stamped on top so
    they aren't classified as target signal even when embedded in nebulosity.
    """
    from scipy.ndimage import gaussian_filter

    h, w = background_subtracted.shape
    if smoothing_sigmas is None:
        # Cap the largest scale at H/16 so segmentation works on small test
        # images and large astro frames alike.
        max_sigma = max(2.0, min(h, w) / 16.0)
        smoothing_sigmas = tuple(s for s in (1.0, 2.0, 4.0, 8.0, 16.0) if s <= max_sigma) or (2.0,)

    mask = np.zeros((h, w), dtype=np.uint8)

    # 1) Multi-scale diffuse signal detection.
    for sigma in smoothing_sigmas:
        smoothed = gaussian_filter(background_subtracted, sigma=sigma)
        # Robust noise estimate: 1.4826 * MAD of central 50% of values
        med = float(np.median(smoothed))
        mad = float(np.median(np.abs(smoothed - med)))
        noise = max(1.4826 * mad, 1e-9)
        signal_mask = smoothed > med + target_threshold_sigma * noise
        mask[signal_mask] = SEG_TARGET

    # 2) Stamp source ellipses on top.
    if sources.raw.shape[0] > 0:
        yy, xx = np.mgrid[0:h, 0:w]
        for rec in sources.raw:
            # Use 3 * a as a safe stamp radius.
            r = max(3.0 * float(rec["a"]), 3.0)
            x = float(rec["x"])
            y = float(rec["y"])
            # Bounding-box test first to keep this fast.
            x0, x1 = int(max(0, x - r)), int(min(w, x + r + 1))
            y0, y1 = int(max(0, y - r)), int(min(h, y + r + 1))
            if x0 >= x1 or y0 >= y1:
                continue
            sub_xx = xx[y0:y1, x0:x1]
            sub_yy = yy[y0:y1, x0:x1]
            within = (sub_xx - x) ** 2 + (sub_yy - y) ** 2 <= r * r
            mask[y0:y1, x0:x1][within] = SEG_SOURCE

    return mask


# ---------------------------------------------------------------------------- #
# Top-level A1 pipeline
# ---------------------------------------------------------------------------- #


def characterize_image(path: str | Path) -> ImageCharacterization:
    """Run A1.1 -> A1.6 end to end on ``path``."""
    log.info("A1: characterizing %s", path)
    luminance, color, meta = load_and_normalize(path)
    background, rms = estimate_background(luminance)
    bg_sub = (luminance - background).astype(np.float32)

    raw = extract_sources(bg_sub, rms)
    log.info("A1: detected %d sources", raw.shape[0])
    classified = classify_sources(raw)
    log.info(
        "A1: classified into %d stars / %d extended / %d artifacts (median PSF size = %.2f px)",
        len(classified.stars),
        len(classified.extended),
        len(classified.artifacts),
        classified.median_psf_size,
    )

    seg = build_segmentation_mask(bg_sub, rms, classified)

    return ImageCharacterization(
        luminance=luminance,
        color=color,
        background=background,
        rms=rms,
        background_subtracted=bg_sub,
        sources=classified,
        segmentation=seg,
        metadata=meta,
    )


__all__ = [
    "ImageCharacterization",
    "ImageMetadata",
    "SEG_BACKGROUND",
    "SEG_SOURCE",
    "SEG_TARGET",
    "SourceCatalog",
    "build_segmentation_mask",
    "characterize_image",
    "classify_sources",
    "estimate_background",
    "extract_sources",
    "load_and_normalize",
]
