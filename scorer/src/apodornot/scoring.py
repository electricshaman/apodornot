"""A7 — Scoring and comparison against APOD reference distributions.

Loads per-category metric distributions from A8, computes percentile rank for
each metric of a submitted image, aggregates into 5 composite axes (star
quality / noise management / detail resolution / gradient control / color
calibration), and renders a radar-chart scorecard plus diagnostic text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from .archive_pipeline import (
    SCORING_METRICS,
    ArchiveDistributions,
    _get_path,
    categorize_entry,
    load_distributions,
)
from .logging import get_logger
from .pipeline import EvaluationResult

log = get_logger("apodornot.scoring")


# ---------------------------------------------------------------------------- #
# Composite axis definitions
# ---------------------------------------------------------------------------- #


# Each axis = (display name, list of (metric, weight)). Weights need not sum to 1;
# we normalize at scoring time.
COMPOSITE_AXES: tuple[tuple[str, tuple[tuple[str, float], ...]], ...] = (
    (
        "Star quality",
        (
            ("median_fwhm_px", 1.0),
            ("median_eccentricity", 1.0),
            ("fwhm_corner_excess", 0.5),
        ),
    ),
    (
        "Noise management",
        (
            ("noise_floor_l", 0.5),
            ("psd_high_band_suppression", 1.0),
            ("autocorr_width_px", 1.0),
            ("snr_target_median", 0.5),
        ),
    ),
    (
        "Detail resolution",
        (
            ("target_spectral_slope", 1.0),
            ("target_effective_resolution", 1.0),
            ("psd_spectral_slope", 0.5),
        ),
    ),
    (
        "Gradient control",
        (
            ("gradient_ratio", 1.0),
            ("vignetting_falloff", 1.0),
        ),
    ),
    (
        "Color calibration",
        (
            ("color_overall_score", 1.0),
            ("star_diversity_score", 1.0),
            ("background_chroma_distance", 0.5),
            ("color_balance_magnitude", 0.5),
        ),
    ),
)


# Per-metric diagnostic text generators. Each takes the percentile and the
# raw value; returns a finding string when the metric is weak (< ~30) or
# strong enough to call out (> ~85), else None. These read like an experienced
# astrophotographer's review.
DIAGNOSTIC_RULES: dict[str, callable] = {}


def _diag(metric: str):
    def decorator(fn):
        DIAGNOSTIC_RULES[metric] = fn
        return fn
    return decorator


@_diag("median_fwhm_px")
def _diag_fwhm(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25:
        return (
            f"FWHM is {value:.2f} px (bottom {pct:.0f}% of reference). Stars are "
            f"unfocused or seeing-limited — check focuser, collimation, and tracking."
        )
    if pct > 85:
        return f"Excellent FWHM ({value:.2f} px, top {100 - pct:.0f}%) — sharp, well-focused stars."
    return None


@_diag("median_eccentricity")
def _diag_ecc(pct: float, value: float, ctx: dict) -> str | None:
    pattern = ctx.get("eccentricity_pattern", "")
    if pct < 25 and value > 0.3:
        if pattern == "uniform_drift":
            return (
                f"Star eccentricity is elevated ({value:.2f}) with consistent orientation — "
                "periodic error in RA tracking or mount drift. FWHM aside, sharper acquisition "
                "needs better autoguiding."
            )
        if pattern == "radial":
            return (
                f"Star eccentricity is elevated ({value:.2f}) with a radial pattern — "
                "coma, field curvature, or tilt. A coma corrector or sensor-tilt adjustment "
                "would help."
            )
        return f"Star eccentricity ({value:.2f}) is in the bottom {pct:.0f}% — likely tracking or optical issue."
    return None


@_diag("fwhm_corner_excess")
def _diag_corner(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25 and value > 0.15:
        return (
            f"Corner stars are {value * 100:.0f}% larger than center — field curvature, tilt, "
            "or coma. Image circle may be too small for the sensor."
        )
    return None


@_diag("psd_high_band_suppression")
def _diag_nr(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25 and value > 0.5:
        return (
            f"Background PSD shows {value * 100:.0f}% high-band suppression — aggressive "
            "luminance noise reduction, giving a 'plastic' look. Lighter NR would preserve "
            "natural noise texture."
        )
    return None


@_diag("autocorr_width_px")
def _diag_ac(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25 and value > 2.0:
        return (
            f"Noise autocorrelation width {value:.2f} px is wider than ideal — drizzling, "
            "resampling, or noise reduction has smeared pixel-level detail."
        )
    return None


@_diag("snr_target_median")
def _diag_snr(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25:
        return f"Target SNR ({value:.1f}) in the bottom {pct:.0f}% — more integration time would help."
    if pct > 85:
        return f"Strong target SNR ({value:.1f}, top {100 - pct:.0f}%) — well-exposed."
    return None


@_diag("gradient_ratio")
def _diag_gradient(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25 and value > 5.0:
        return (
            f"Sky gradient is {value:.1f}x the noise floor — light pollution gradient or "
            "incomplete flat-field correction. Try gradient removal in processing or improve "
            "calibration frames."
        )
    return None


@_diag("vignetting_falloff")
def _diag_vignette(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25 and value > 0.1:
        return (
            f"Residual vignetting ({value * 100:.0f}% center-to-corner falloff) — flat-field "
            "calibration is incomplete or missing."
        )
    return None


@_diag("color_overall_score")
def _diag_color(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25:
        return (
            f"Color calibration is in the bottom {pct:.0f}% — star colors look uniform, "
            "background may be color-cast, or palette mapping is too aggressive."
        )
    return None


@_diag("star_diversity_score")
def _diag_star_div(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25 and value < 0.3:
        return (
            "Star colors collapsed to nearly monochrome — color information was destroyed "
            "in processing (heavy saturation, white-balance, or noise reduction on the chroma channels)."
        )
    return None


@_diag("background_chroma_distance")
def _diag_bgchroma(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25 and value > 0.04:
        return (
            f"Background has a noticeable color cast (chroma distance {value:.3f}) — "
            "background neutralization step in processing would help."
        )
    return None


# ---------------------------------------------------------------------------- #
# Result dataclasses
# ---------------------------------------------------------------------------- #


@dataclass
class MetricScore:
    metric: str
    value: float | None
    percentile: float            # 0..100, higher = better
    higher_is_better: bool
    raw_quantiles: dict[str, float] = field(default_factory=dict)


@dataclass
class AxisScore:
    axis: str
    score: float                 # 0..100
    components: list[MetricScore] = field(default_factory=list)


@dataclass
class ScoreCard:
    image_path: str
    target_category: str
    reference_category: str
    reference_n: int
    axis_scores: list[AxisScore]
    metric_scores: list[MetricScore]
    diagnostics: list[str]
    overall_score: float

    def text_report(self) -> str:
        lines = [
            f"apodornot scorecard — {self.image_path}",
            f"  Reference set: {self.reference_category} (n={self.reference_n})",
            f"  Overall: {self.overall_score:.0f}/100",
            "",
            "Axes:",
        ]
        for ax in self.axis_scores:
            lines.append(f"  {ax.axis:22s} {ax.score:5.1f}/100")
        lines.append("")
        if self.diagnostics:
            lines.append("Findings:")
            for d in self.diagnostics:
                lines.append(f"  - {d}")
        else:
            lines.append("Findings: (no notable weaknesses)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------- #
# Reference loading (A7.1)
# ---------------------------------------------------------------------------- #


def default_distributions_path() -> Path:
    """Path to the bundled reference distributions JSON."""
    return Path(resources.files("apodornot").joinpath("data/reference_distributions.json"))


def load_default_distributions() -> ArchiveDistributions | None:
    p = default_distributions_path()
    if not p.exists():
        return None
    return load_distributions(p)


def select_reference_set(
    distributions: ArchiveDistributions, target_type: str | None, *, min_n: int = 8
) -> tuple[str, dict[str, Any]]:
    """Choose the reference category. Falls back to 'global' if too few samples."""
    candidate = (target_type or "global").strip().lower().replace(" ", "_") or "global"
    metrics = distributions.by_category.get(candidate)
    if metrics is None:
        candidate = "global"
        metrics = distributions.by_category.get("global", {})
    # Check sample size — if any metric in the chosen category has too few entries
    # we fall back to the global pool.
    if metrics:
        max_n = max((d.n for d in metrics.values()), default=0)
        if max_n < min_n and "global" in distributions.by_category:
            candidate = "global"
            metrics = distributions.by_category["global"]
    return candidate, metrics


# ---------------------------------------------------------------------------- #
# Per-metric scoring (A7.2)
# ---------------------------------------------------------------------------- #


def score_metrics(
    summary: dict[str, Any], reference_metrics: dict[str, Any]
) -> list[MetricScore]:
    out: list[MetricScore] = []
    for metric_name, metric_path, _hib in SCORING_METRICS:
        v = _get_path(summary, metric_path)
        ref = reference_metrics.get(metric_name)
        if ref is None or ref.n == 0:
            out.append(
                MetricScore(
                    metric=metric_name,
                    value=v,
                    percentile=float("nan"),
                    higher_is_better=_hib,
                    raw_quantiles={},
                )
            )
            continue
        if v is None:
            out.append(
                MetricScore(
                    metric=metric_name,
                    value=None,
                    percentile=float("nan"),
                    higher_is_better=ref.higher_is_better,
                    raw_quantiles=ref.quantiles,
                )
            )
            continue
        pct = ref.percentile(v)
        out.append(
            MetricScore(
                metric=metric_name,
                value=float(v),
                percentile=float(pct),
                higher_is_better=ref.higher_is_better,
                raw_quantiles=ref.quantiles,
            )
        )
    return out


# ---------------------------------------------------------------------------- #
# Composite axes + radar chart (A7.3)
# ---------------------------------------------------------------------------- #


def aggregate_axes(metric_scores: list[MetricScore]) -> list[AxisScore]:
    by_name = {m.metric: m for m in metric_scores}
    out: list[AxisScore] = []
    for axis_name, components in COMPOSITE_AXES:
        weighted_sum = 0.0
        weight_total = 0.0
        comps: list[MetricScore] = []
        for metric_name, weight in components:
            ms = by_name.get(metric_name)
            if ms is None or not np.isfinite(ms.percentile):
                continue
            weighted_sum += ms.percentile * weight
            weight_total += weight
            comps.append(ms)
        if weight_total > 0:
            score = weighted_sum / weight_total
        else:
            score = float("nan")
        out.append(AxisScore(axis=axis_name, score=score, components=comps))
    return out


def render_radar_chart(scorecard: ScoreCard, out_path: str | Path) -> Path:
    """Render the scorecard as a radar chart PNG. Requires matplotlib."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path = Path(out_path)
    labels = [ax.axis for ax in scorecard.axis_scores]
    values = [ax.score if np.isfinite(ax.score) else 0.0 for ax in scorecard.axis_scores]

    n = len(labels)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    values_loop = values + values[:1]
    angles_loop = angles + angles[:1]

    fig = plt.figure(figsize=(7, 7))
    ax = fig.add_subplot(111, polar=True)
    ax.plot(angles_loop, values_loop, linewidth=2, color="#1f77b4")
    ax.fill(angles_loop, values_loop, color="#1f77b4", alpha=0.25)
    ax.set_xticks(angles)
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], fontsize=8)
    ax.set_rlabel_position(90)
    ax.set_title(
        f"{Path(scorecard.image_path).name} — overall {scorecard.overall_score:.0f}/100\n"
        f"vs APOD '{scorecard.reference_category}' (n={scorecard.reference_n})",
        fontsize=12,
        pad=20,
    )
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------- #
# Diagnostics (A7.4)
# ---------------------------------------------------------------------------- #


def generate_diagnostics(
    metric_scores: list[MetricScore], evaluation: EvaluationResult
) -> list[str]:
    ctx = {
        "eccentricity_pattern": (
            evaluation.star_field.field_variation.eccentricity_pattern
            if evaluation.star_field.field_variation
            else "unknown"
        ),
    }
    findings: list[str] = []
    for ms in metric_scores:
        if not np.isfinite(ms.percentile) or ms.value is None:
            continue
        rule = DIAGNOSTIC_RULES.get(ms.metric)
        if rule is None:
            continue
        out = rule(ms.percentile, ms.value, ctx)
        if out:
            findings.append(out)
    return findings


# ---------------------------------------------------------------------------- #
# Top-level A7 entrypoint
# ---------------------------------------------------------------------------- #


def score_evaluation(
    evaluation: EvaluationResult,
    *,
    target_type: str | None = None,
    distributions: ArchiveDistributions | None = None,
) -> ScoreCard:
    """Score an A1..A6 evaluation against the APOD reference distributions."""
    if distributions is None:
        distributions = load_default_distributions()
    if distributions is None:
        raise RuntimeError(
            "No reference distributions available. Run `apodornot build-archive` first."
        )

    summary = evaluation.to_summary()

    # Resolve target category — explicit beats auto-detected.
    if target_type:
        target_category = target_type
    else:
        title_guess = Path(evaluation.image_path).stem
        target_category = categorize_entry(title_guess)
    reference_category, metrics = select_reference_set(distributions, target_category)
    reference_n = max((d.n for d in metrics.values()), default=0)

    metric_scores = score_metrics(summary, metrics)
    axis_scores = aggregate_axes(metric_scores)
    diagnostics = generate_diagnostics(metric_scores, evaluation)

    valid_axis_scores = [ax.score for ax in axis_scores if np.isfinite(ax.score)]
    overall = float(np.mean(valid_axis_scores)) if valid_axis_scores else float("nan")

    return ScoreCard(
        image_path=evaluation.image_path,
        target_category=target_category,
        reference_category=reference_category,
        reference_n=reference_n,
        axis_scores=axis_scores,
        metric_scores=metric_scores,
        diagnostics=diagnostics,
        overall_score=overall,
    )


__all__ = [
    "AxisScore",
    "COMPOSITE_AXES",
    "DIAGNOSTIC_RULES",
    "MetricScore",
    "ScoreCard",
    "aggregate_axes",
    "generate_diagnostics",
    "load_default_distributions",
    "render_radar_chart",
    "score_evaluation",
    "score_metrics",
    "select_reference_set",
]
