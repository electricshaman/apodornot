"""A6 — Color analysis.

Three measurements: star color accuracy (correlation with stellar B-V color
distribution), background neutrality (chromaticity distance from gray), and
target color distribution / narrowband palette detection.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .image_chars import SEG_BACKGROUND, SEG_TARGET, ImageCharacterization
from .logging import get_logger
from .star_field import StarFieldResult

log = get_logger("apodornot.color")


# ---------------------------------------------------------------------------- #
# Data model
# ---------------------------------------------------------------------------- #


@dataclass
class StarColorAccuracy:
    n_stars: int
    color_diversity: float          # variance of B/R ratios
    diversity_score: float          # mapped to [0, 1] — 0 = monochrome, 1 = realistic spread
    median_br_ratio: float          # median B/R ratio


@dataclass
class BackgroundNeutrality:
    chroma_distance: float          # Euclidean distance in (r, g) chromaticity from neutral
    rg_chroma: float
    bg_chroma: float


@dataclass
class TargetColorDistribution:
    palette: str                    # "broadband_rgb" | "sho" | "hoo" | "natural" | "unknown"
    palette_confidence: float       # 0..1
    target_color_means: dict[str, float]
    target_color_stds: dict[str, float]
    chroma_concentration: float = 0.0  # 0..1; high = single hue dominates target chromaticity


@dataclass
class ColorResult:
    star_accuracy: StarColorAccuracy | None
    background: BackgroundNeutrality | None
    target_distribution: TargetColorDistribution | None
    overall_score: float            # 0..1, composite

    def summary(self) -> dict:
        return {
            "star_diversity_score": self.star_accuracy.diversity_score if self.star_accuracy else None,
            "background_chroma_distance": self.background.chroma_distance if self.background else None,
            "target_palette": self.target_distribution.palette if self.target_distribution else None,
            "palette_confidence": self.target_distribution.palette_confidence if self.target_distribution else None,
            "chroma_concentration": self.target_distribution.chroma_concentration if self.target_distribution else None,
            "overall_score": self.overall_score,
        }


# ---------------------------------------------------------------------------- #
# A6.1 — Star color accuracy
# ---------------------------------------------------------------------------- #


def evaluate_star_colors(star_field: StarFieldResult) -> StarColorAccuracy | None:
    """Quantify how diverse star colors are.

    Real, well-calibrated star fields show a spread of B/R flux ratios driven by
    the stellar temperature distribution (B-V indices roughly span 0.3..3 for
    O-type to M-type stars). When color processing destroys star colors, all
    stars collapse to ~ neutral.
    """
    colors = star_field.star_colors
    if not colors:
        return None
    rgb = np.asarray(colors, dtype=np.float64)
    # Normalize each row to sum=1 to make this color-only (not flux-only).
    rsum = rgb.sum(axis=1, keepdims=True)
    rsum = np.where(rsum > 1e-9, rsum, 1e-9)
    rgb_norm = rgb / rsum

    r = rgb_norm[:, 0]
    b = rgb_norm[:, 2]
    br = b / np.where(r > 1e-9, r, 1e-9)
    median_br = float(np.median(br))

    # Use IQR of normalized R, G, B as a robust diversity measure.
    iqrs = np.array(
        [
            float(np.percentile(rgb_norm[:, i], 75) - np.percentile(rgb_norm[:, i], 25))
            for i in range(3)
        ]
    )
    diversity = float(np.mean(iqrs))
    # Map to an unclipped score: empirically, IQR ~0.05 = realistic spread.
    # The previous version clipped at 1.0, which saturated most well-processed
    # images and made the reference distribution useless above the 50th
    # percentile. Without the clip, scores >1.0 are possible and the
    # percentile rank stays meaningful across the whole reference set.
    score = float(diversity / 0.05)

    return StarColorAccuracy(
        n_stars=len(colors),
        color_diversity=diversity,
        diversity_score=score,
        median_br_ratio=median_br,
    )


# ---------------------------------------------------------------------------- #
# A6.2 — Background neutrality
# ---------------------------------------------------------------------------- #


def measure_background_neutrality(
    color: np.ndarray, segmentation: np.ndarray
) -> BackgroundNeutrality | None:
    """Distance of background chromaticity from neutral gray.

    Uses (r, g) = (R/(R+G+B), G/(R+G+B)) chromaticity. Neutral gray is at
    (1/3, 1/3). Returns Euclidean distance plus the rg/bg components for
    diagnostic interpretation.
    """
    if color is None:
        return None
    bg_mask = segmentation == SEG_BACKGROUND
    if not np.any(bg_mask):
        return None

    R = float(np.median(color[..., 0][bg_mask]))
    G = float(np.median(color[..., 1][bg_mask]))
    B = float(np.median(color[..., 2][bg_mask]))
    total = R + G + B
    if total < 1e-9:
        return BackgroundNeutrality(0.0, 0.0, 0.0)
    r = R / total
    g = G / total
    b = B / total

    rg_chroma = abs(r - g)
    bg_chroma = abs(b - g)
    chroma_distance = float(np.hypot(r - 1 / 3, g - 1 / 3))
    return BackgroundNeutrality(
        chroma_distance=chroma_distance, rg_chroma=rg_chroma, bg_chroma=bg_chroma
    )


# ---------------------------------------------------------------------------- #
# A6.3 — Target color distribution and palette detection
# ---------------------------------------------------------------------------- #


def _channel_target_stats(color: np.ndarray, segmentation: np.ndarray) -> tuple[dict, dict] | None:
    target_mask = segmentation == SEG_TARGET
    if not np.any(target_mask):
        return None
    means = {}
    stds = {}
    for i, name in enumerate(("R", "G", "B")):
        ch = color[..., i][target_mask]
        means[name] = float(np.mean(ch))
        stds[name] = float(np.std(ch))
    return means, stds


def detect_palette(
    color: np.ndarray, segmentation: np.ndarray
) -> TargetColorDistribution | None:
    """Classify the dominant target color signature.

    Heuristics derived from the typical color-channel histograms of:
      - Broadband RGB: hydrogen-alpha pushes R; OIII pushes G/B; balanced overall.
      - SHO (Hubble) palette: SII -> R, Ha -> G, OIII -> B; G dominant, R-G correlated.
      - HOO: Ha -> R, OIII -> G+B; R high, G ~= B.
      - Natural / desaturated: all channels track each other closely.
    """
    stats = _channel_target_stats(color, segmentation)
    if stats is None:
        return None
    means, stds = stats
    total = sum(means.values())
    if total < 1e-9:
        return TargetColorDistribution(
            palette="unknown", palette_confidence=0.0, target_color_means=means, target_color_stds=stds
        )

    r = means["R"] / total
    g = means["G"] / total
    b = means["B"] / total
    # Cross-channel correlations on a sample of target pixels for palette detection.
    target_mask = segmentation == SEG_TARGET
    rng = np.random.default_rng(0xA90D)
    pix = np.argwhere(target_mask)
    if len(pix) > 5000:
        pix = pix[rng.choice(len(pix), 5000, replace=False)]
    sample_r = color[pix[:, 0], pix[:, 1], 0]
    sample_g = color[pix[:, 0], pix[:, 1], 1]
    sample_b = color[pix[:, 0], pix[:, 1], 2]
    corr_rg = float(np.corrcoef(sample_r, sample_g)[0, 1]) if len(sample_r) > 1 else 0.0
    corr_gb = float(np.corrcoef(sample_g, sample_b)[0, 1]) if len(sample_r) > 1 else 0.0
    corr_rb = float(np.corrcoef(sample_r, sample_b)[0, 1]) if len(sample_r) > 1 else 0.0

    palette = "unknown"
    confidence = 0.5

    if corr_rg > 0.85 and corr_gb > 0.85 and corr_rb > 0.85:
        # All channels track each other -> nearly mono / heavily desaturated.
        palette = "natural"
        confidence = float(min(corr_rg, corr_gb, corr_rb))
    elif g > 0.42 and corr_rg > 0.5 and corr_gb < 0.6:
        palette = "sho"
        confidence = float(0.6 + 0.4 * (g - 0.4))
    elif r > 0.4 and abs(g - b) / max(g + b, 1e-9) < 0.15 and corr_gb > 0.6:
        palette = "hoo"
        confidence = float(0.6 + 0.4 * min(r - 0.35, 1.0))
    else:
        palette = "broadband_rgb"
        # Confidence higher when channels are well-balanced and decorrelated.
        confidence = float(0.5 + 0.5 * (1 - abs(r - 1 / 3) - abs(g - 1 / 3) - abs(b - 1 / 3)))

    # Chromaticity concentration — measures how much the target's chromaticity
    # piles up in a single hue cluster vs spreading across the primaries.
    # The "magenta cast across bright filaments" pattern shows up as ~70% of
    # target pixels falling in a single hue bucket.
    chroma_conc = _chroma_concentration(color, target_mask, sample_r, sample_g, sample_b)

    return TargetColorDistribution(
        palette=palette,
        palette_confidence=float(np.clip(confidence, 0.0, 1.0)),
        target_color_means=means,
        target_color_stds=stds,
        chroma_concentration=chroma_conc,
    )


def _chroma_concentration(
    color: np.ndarray,
    target_mask: np.ndarray,
    sample_r: np.ndarray,
    sample_g: np.ndarray,
    sample_b: np.ndarray,
) -> float:
    """Compute hue-histogram concentration over target pixels.

    Build a 12-bin hue histogram (excluding near-neutral pixels) and return
    the fraction of pixels in the single dominant bin. Uniform RGB nebulae
    spread across multiple hue bins (low concentration); narrowband palettes
    that collapse to a single hue cluster (e.g. magenta-everywhere or
    teal-everywhere) score high.
    """
    if sample_r.size == 0:
        return 0.0
    rgb = np.stack([sample_r, sample_g, sample_b], axis=1).astype(np.float64)
    total = rgb.sum(axis=1)
    valid = total > 1e-6
    if not np.any(valid):
        return 0.0
    rgb = rgb[valid] / total[valid, None]

    # Drop near-neutral pixels (low chroma) — they aren't part of the palette.
    neutral_dist = np.hypot(rgb[:, 0] - 1 / 3, rgb[:, 1] - 1 / 3)
    chromatic = rgb[neutral_dist > 0.04]
    if chromatic.shape[0] < 50:
        return 0.0

    # Hue from chromaticity (r, g) using atan2 in the (r-1/3, g-1/3) plane.
    rx = chromatic[:, 0] - 1 / 3
    gx = chromatic[:, 1] - 1 / 3
    hues = np.arctan2(gx, rx)  # [-pi, pi]
    bins = np.linspace(-np.pi, np.pi, 13)  # 12 hue bins
    counts, _ = np.histogram(hues, bins=bins)
    return float(counts.max() / counts.sum())


# ---------------------------------------------------------------------------- #
# Top-level A6 pipeline
# ---------------------------------------------------------------------------- #


def analyze_color(
    chars: ImageCharacterization, star_field: StarFieldResult
) -> ColorResult:
    if chars.color is None:
        return ColorResult(None, None, None, overall_score=float("nan"))

    star_acc = evaluate_star_colors(star_field)
    bg_neutral = measure_background_neutrality(chars.color, chars.segmentation)
    target_dist = detect_palette(chars.color, chars.segmentation)

    # Composite score: average of available components, each in [0, 1].
    components = []
    if star_acc:
        components.append(star_acc.diversity_score)
    if bg_neutral:
        # Neutrality: distance 0 = perfect, distance 0.1+ = bad.
        components.append(float(np.clip(1.0 - bg_neutral.chroma_distance / 0.1, 0.0, 1.0)))
    if target_dist:
        components.append(target_dist.palette_confidence)
    overall = float(np.mean(components)) if components else float("nan")

    log.info(
        "A6: star diversity=%.2f, bg chroma=%.3f, palette=%s, overall=%.2f",
        star_acc.diversity_score if star_acc else float("nan"),
        bg_neutral.chroma_distance if bg_neutral else float("nan"),
        target_dist.palette if target_dist else "n/a",
        overall,
    )

    return ColorResult(
        star_accuracy=star_acc,
        background=bg_neutral,
        target_distribution=target_dist,
        overall_score=overall,
    )


__all__ = [
    "BackgroundNeutrality",
    "ColorResult",
    "StarColorAccuracy",
    "TargetColorDistribution",
    "analyze_color",
    "detect_palette",
    "evaluate_star_colors",
    "measure_background_neutrality",
]
