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
            ("fwhm_quadrant_asymmetry", 0.5),
        ),
    ),
    (
        "Noise management",
        (
            ("noise_floor_l", 0.5),
            ("psd_high_band_suppression", 1.0),
            ("autocorr_width_px", 1.0),
            ("snr_target_median", 0.5),
            ("fpn_max_pattern", 0.75),
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
            ("chroma_concentration", 0.5),
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


def _md(heading: str, body: str) -> str:
    """Format a finding as a markdown ### heading + body paragraph."""
    return f"### {heading}\n\n{body}"


@_diag("median_fwhm_px")
def _diag_fwhm(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 25:
        return _md(
            f"Star FWHM — {pct:.0f}th percentile",
            f"FWHM is **{value:.2f} px** (bottom {pct:.0f}% of reference). Stars are "
            "unfocused or seeing-limited — check focuser, collimation, and tracking.",
        )
    if pct > 85:
        return _md(
            f"Star FWHM — {pct:.0f}th percentile (top tier)",
            f"Excellent FWHM (**{value:.2f} px**, top {100 - pct:.0f}%) — sharp, "
            "well-focused stars.",
        )
    return None


@_diag("median_eccentricity")
def _diag_ecc(pct: float, value: float, ctx: dict) -> str | None:
    pattern = ctx.get("eccentricity_pattern", "")
    if pct < 50 and value > 0.3:
        if pattern == "uniform_drift":
            return _md(
                f"Star eccentricity — {pct:.0f}th percentile (tracking)",
                f"Eccentricity is **{value:.2f}** with a *uniform* orientation across the field — "
                "the signature of periodic error in RA tracking or mount drift. FWHM aside, "
                "sharper acquisition needs better autoguiding.",
            )
        if pattern == "radial":
            return _md(
                f"Star eccentricity — {pct:.0f}th percentile (optical aberration)",
                f"Eccentricity is **{value:.2f}** with a *radial* pattern — coma, field "
                "curvature, or sensor tilt. A coma corrector or sensor-tilt adjustment would help.",
            )
        return _md(
            f"Star eccentricity — {pct:.0f}th percentile",
            f"Stars are noticeably elongated (median ecc **{value:.2f}**) with a "
            "`random_seeing` pattern — atmosphere is the most likely cause. The lever is "
            "subframe weighting: cull the worst 30% by FWHM/ecc before integration so the "
            "stack inherits the better-seeing nights.",
        )
    return None


@_diag("fwhm_corner_excess")
def _diag_corner(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 50 and value > 0.15:
        return _md(
            f"Corner FWHM excess — {pct:.0f}th percentile",
            f"Corner stars are **{value * 100:.0f}% larger** than center — field curvature, "
            "tilt, or coma. Image circle may be too small for the sensor; a coma corrector "
            "or flattener should help.",
        )
    return None


@_diag("fwhm_quadrant_asymmetry")
def _diag_quadrant_asymmetry(pct: float, value: float, ctx: dict) -> str | None:
    """Asymmetric corner-to-corner FWHM is a tilt fingerprint that the radial
    corner-excess metric misses (a tilted sensor has one corner much worse than
    the diagonally opposite one)."""
    quadrants = ctx.get("quadrants") or {}
    if pct < 50 and value > 0.15 and quadrants:
        # Find worst quadrant for the diagnostic text.
        finite = [(k, q.get("median_fwhm_px"))
                  for k, q in quadrants.items()
                  if isinstance(q, dict) and isinstance(q.get("median_fwhm_px"), (int, float))]
        if finite:
            worst_label, worst_fwhm = max(finite, key=lambda kv: kv[1])
            best_label, best_fwhm = min(finite, key=lambda kv: kv[1])
            quadrant_name = {
                "tl": "top-left", "tr": "top-right",
                "bl": "bottom-left", "br": "bottom-right",
            }
            return _md(
                f"FWHM quadrant asymmetry — {pct:.0f}th percentile",
                f"Star sharpness varies **{value * 100:.0f}%** across the frame, with the "
                f"{quadrant_name.get(worst_label, worst_label)} quadrant ({worst_fwhm:.2f} px) "
                f"noticeably worse than {quadrant_name.get(best_label, best_label)} "
                f"({best_fwhm:.2f} px). That asymmetry across the diagonal is a sensor-tilt "
                "fingerprint — uniform field curvature would degrade all four corners equally. "
                "Worth a tilt-plate adjustment before the next session.",
            )
    return None


@_diag("psd_high_band_suppression")
def _diag_nr(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 50 and value > 0.5:
        return _md(
            f"High-frequency noise suppression — {pct:.0f}th percentile",
            f"Background PSD shows **{value * 100:.0f}% high-band suppression** — aggressive "
            "luminance noise reduction giving a 'plastic' look. Lighter NR would preserve "
            "natural noise texture; consider luminance-masked NR so faint regions get cleaned "
            "without flattening the brights.",
        )
    return None


@_diag("fpn_max_pattern")
def _diag_fpn(pct: float, value: float, ctx: dict) -> str | None:
    """Excess low-frequency energy in column-mean or row-mean PSD = fixed
    pattern noise (amp glow, vertical/horizontal striping, residual flat-field)."""
    if pct < 50 and value > 0.4:
        return _md(
            f"Fixed-pattern noise — {pct:.0f}th percentile",
            f"The background carries a row- or column-aligned pattern (low-frequency "
            f"energy fraction {value:.2f}) that's invisible to radial PSD or 2D "
            "autocorrelation but visible as horizontal stripes, vertical bars, or "
            "residual amp glow in the dark sky. Likely culprits: "
            "missing/wrong dark frames, missing/wrong bias frames, sensor amp glow "
            "near a corner, or fixed-pattern residue from a flat that doesn't match the lights. "
            "Recapture darks at the current exposure/temperature and re-run calibration.",
        )
    return None


@_diag("autocorr_width_px")
def _diag_ac(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 50 and value > 2.0:
        return _md(
            f"Noise autocorrelation — {pct:.0f}th percentile",
            f"Noise autocorrelation width **{value:.2f} px** is wider than ideal — drizzling, "
            "resampling, or NR has smeared pixel-level detail. If you're not undersampled, "
            "skip drizzle; if you resampled, do it once and at the final stage.",
        )
    return None


@_diag("snr_target_median")
def _diag_snr(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 50:
        return _md(
            f"Target SNR — {pct:.0f}th percentile",
            f"Target SNR (**{value:.1f}**) sits below the reference median. More integration "
            "time is the most direct fix — each doubling of subs cuts noise by √2. Narrowband "
            "filters or a darker site help if you're light-pollution-limited.",
        )
    if pct > 85:
        return _md(
            f"Target SNR — {pct:.0f}th percentile (top tier)",
            f"Strong target SNR (**{value:.1f}**, top {100 - pct:.0f}%) — well-exposed.",
        )
    return None


@_diag("gradient_ratio")
def _diag_gradient(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 50 and value > 5.0:
        return _md(
            f"Sky gradient — {pct:.0f}th percentile",
            f"Sky gradient is **{value:.1f}× the noise floor** — light-pollution gradient or "
            "incomplete flat-field correction. Try gradient removal in processing (DBE, "
            "GraXpert) or recapture flats matching the current optical train.",
        )
    return None


@_diag("vignetting_falloff")
def _diag_vignette(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 50 and value > 0.1:
        return _md(
            f"Residual vignetting — {pct:.0f}th percentile",
            f"Residual vignetting (**{value * 100:.0f}% center-to-corner falloff**) — "
            "flat-field calibration is incomplete or doesn't match the current optical setup. "
            "Recapture flats with the current focuser position and filter.",
        )
    return None


@_diag("color_overall_score")
def _diag_color(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 50:
        return _md(
            f"Color calibration — {pct:.0f}th percentile",
            f"Color calibration sits at the **{pct:.0f}th percentile** — star colors may be "
            "uniform, background may carry a cast, or palette mapping is heavier than typical. "
            "Try background neutralization + photometric color calibration before saturation moves.",
        )
    return None


@_diag("star_diversity_score")
def _diag_star_div(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 50 and value < 0.3:
        return _md(
            f"Star color diversity — {pct:.0f}th percentile",
            "Star colors collapsed to nearly monochrome — color information was destroyed in "
            "processing (heavy saturation, white-balance, or NR on the chroma channels). "
            "Preserve color earlier in the pipeline; saturation should come *last*.",
        )
    return None


@_diag("chroma_concentration")
def _diag_chroma_conc(pct: float, value: float, ctx: dict) -> str | None:
    """High chroma concentration = single hue dominates target chromaticity.
    Common for narrowband images where one channel pair eats the palette
    (e.g. magenta-everywhere SII+Ha vs OIII separation)."""
    if pct < 50 and value > 0.4:
        return _md(
            f"Chroma concentration — {pct:.0f}th percentile",
            f"**{value * 100:.0f}%** of target-pixel chromaticity falls in a single hue cluster. "
            "The palette has collapsed toward one color (commonly magenta dominance from "
            "Ha+SII running together, or teal from OIII+Ha overlap). The fix is in the "
            "narrowband-channel composition step: rebalance the SII/Ha/OIII contribution "
            "weights to spread the palette across more of the chromaticity plane, or apply "
            "selective hue rotation through PixInsight CurvesTransformation in CIE-Lab on the "
            "a/b channels.",
        )
    return None


@_diag("background_chroma_distance")
def _diag_bgchroma(pct: float, value: float, ctx: dict) -> str | None:
    if pct < 50 and value > 0.04:
        return _md(
            f"Background color cast — {pct:.0f}th percentile",
            f"Background has a noticeable color cast (chroma distance **{value:.3f}**) — "
            "background neutralization step in processing would help. Sample neutral sky "
            "patches in the four corners.",
        )
    return None


# ---------------------------------------------------------------------------- #
# Result dataclasses
# ---------------------------------------------------------------------------- #


@dataclass
class MetricScore:
    metric: str
    value: float | None
    # The metric's direction-corrected score on a 0..100 scale where 100 = best,
    # regardless of higher_is_better. Despite its history, this is **not** a
    # statistical percentile rank — see f1i / docs/design.md for the rename to
    # ``rank_score``. The ``percentile`` property below is a deprecated alias.
    rank_score: float
    higher_is_better: bool
    raw_quantiles: dict[str, float] = field(default_factory=dict)
    label: str = ""              # human-readable display name (e.g. "Median FWHM")
    unit: str = ""               # display unit (e.g. "px", "cy/px")
    format: str = "auto"         # number formatting hint: decimal | scientific | px | auto

    @property
    def percentile(self) -> float:
        """Deprecated alias for ``rank_score``. Will be removed after the
        React UI is ported to LiveView (apodornot-f1i)."""
        return self.rank_score


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
    warnings: list[str] = field(default_factory=list)
    input_domain: str = "unknown"          # "display" / "linear" / "unknown"
    reference_domain: str = "display"      # baseline is APOD JPEGs
    # True when ``target_category`` came from the user-supplied target_type
    # parameter; False when it was auto-derived from the filename. Surfaced
    # in the UI so the user can confirm their pick was honored.
    target_explicit: bool = False

    def text_report(self) -> str:
        lines = [f"apodornot scorecard — {self.image_path}"]
        if self.warnings:
            lines.append("")
            lines.append("⚠ Warnings:")
            for w in self.warnings:
                lines.append(f"  - {w}")
        lines.extend(
            [
                "",
                f"  Reference set: {self.reference_category} (n={self.reference_n}, "
                f"domain={self.reference_domain})",
                f"  Input domain:  {self.input_domain}",
                f"  Overall: {self.overall_score:.0f}/100",
                "",
                "Axes:",
            ]
        )
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
    distributions: ArchiveDistributions,
    target_type: str | None,
    *,
    min_n: int = 3,
    explicit: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Choose the reference category.

    ``explicit=True`` means the user typed the target_type themselves; in that
    case we honor their choice down to ``min_n=1`` and let the small-n caveat
    surface as a warning rather than silently substituting global. With
    ``explicit=False`` (auto-detected from filename), we fall back to the
    global pool when the candidate has fewer than ``min_n`` entries.

    The previous default ``min_n=8`` was too aggressive given the bundled
    distribution's per-category sizes — categories like emission_nebula (6)
    or supernova_remnant (4) all silently substituted to the 100-image
    global pool, hiding the user's explicit choice.
    """
    candidate = (target_type or "global").strip().lower().replace(" ", "_") or "global"
    metrics = distributions.by_category.get(candidate)
    if metrics is None:
        candidate = "global"
        metrics = distributions.by_category.get("global", {})
    if not metrics:
        return candidate, metrics

    max_n = max((d.n for d in metrics.values()), default=0)

    # Explicit user choice: honor down to n=1, never silently fall back.
    if explicit:
        return candidate, metrics

    # Auto-detected: fall back to global if too few samples.
    if max_n < min_n and "global" in distributions.by_category:
        return "global", distributions.by_category["global"]

    return candidate, metrics


# ---------------------------------------------------------------------------- #
# Per-metric scoring (A7.2)
# ---------------------------------------------------------------------------- #


def score_metrics(
    summary: dict[str, Any], reference_metrics: dict[str, Any]
) -> list[MetricScore]:
    from .metric_display import display_for

    out: list[MetricScore] = []
    for metric_name, metric_path, _hib in SCORING_METRICS:
        v = _get_path(summary, metric_path)
        ref = reference_metrics.get(metric_name)
        disp = display_for(metric_name)

        if ref is None or ref.n == 0:
            out.append(
                MetricScore(
                    metric=metric_name,
                    value=v,
                    rank_score=float("nan"),
                    higher_is_better=_hib,
                    raw_quantiles={},
                    label=disp["label"],
                    unit=disp["unit"],
                    format=disp["format"],
                )
            )
            continue
        if v is None:
            out.append(
                MetricScore(
                    metric=metric_name,
                    value=None,
                    rank_score=float("nan"),
                    higher_is_better=ref.higher_is_better,
                    raw_quantiles=ref.quantiles,
                    label=disp["label"],
                    unit=disp["unit"],
                    format=disp["format"],
                )
            )
            continue
        pct = ref.percentile(v)
        out.append(
            MetricScore(
                metric=metric_name,
                value=float(v),
                rank_score=float(pct),
                higher_is_better=ref.higher_is_better,
                raw_quantiles=ref.quantiles,
                label=disp["label"],
                unit=disp["unit"],
                format=disp["format"],
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
    fv = evaluation.star_field.field_variation
    sf_summary = evaluation.star_field.summary()
    ctx = {
        "eccentricity_pattern": fv.eccentricity_pattern if fv else "unknown",
        "quadrants": sf_summary.get("quadrants") or {},
        "snr_per_quadrant": evaluation.noise.snr_per_quadrant if evaluation.noise else {},
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
    log.info("A7: scoring start")
    if distributions is None:
        log.info("A7: loading default distributions")
        distributions = load_default_distributions()
    if distributions is None:
        raise RuntimeError(
            "No reference distributions available. Run `apodornot build-archive` first."
        )

    log.info("A7: building summary (incl. diagnostics payload)")
    summary = evaluation.to_summary()
    log.info("A7: summary built; keys=%s", list(summary.keys()))

    # Resolve target category — explicit beats auto-detected.
    explicit = bool(target_type)
    if explicit:
        target_category = target_type
    else:
        title_guess = Path(evaluation.image_path).stem
        target_category = categorize_entry(title_guess)
    reference_category, metrics = select_reference_set(
        distributions, target_category, explicit=explicit
    )
    reference_n = max((d.n for d in metrics.values()), default=0)
    log.info(
        "A7: reference set = %s (n=%d), scoring metrics",
        reference_category,
        reference_n,
    )

    metric_scores = score_metrics(summary, metrics)
    log.info("A7: scored %d metrics, aggregating axes", len(metric_scores))
    axis_scores = aggregate_axes(metric_scores)
    log.info("A7: generating diagnostics")
    diagnostics = generate_diagnostics(metric_scores, evaluation)
    log.info("A7: diagnostics done (%d findings)", len(diagnostics))

    valid_axis_scores = [ax.score for ax in axis_scores if np.isfinite(ax.score)]
    overall = float(np.mean(valid_axis_scores)) if valid_axis_scores else float("nan")

    input_domain = evaluation.image_chars.metadata.input_domain
    warnings = _domain_warnings(input_domain, reference_domain="display")
    # Surface small-n caveats when the chosen reference set is below ~10 entries.
    if reference_n < 10 and reference_category != "global":
        warnings.append(
            f"Small reference set: only {reference_n} APOD images in the "
            f"'{reference_category}' bucket. Percentile ranks may be noisy. "
            "Treat this as directional rather than precise."
        )

    return ScoreCard(
        image_path=evaluation.image_path,
        target_category=target_category,
        target_explicit=explicit,
        reference_category=reference_category,
        reference_n=reference_n,
        axis_scores=axis_scores,
        metric_scores=metric_scores,
        diagnostics=diagnostics,
        overall_score=overall,
        warnings=warnings,
        input_domain=input_domain,
        reference_domain="display",
    )


def _domain_warnings(input_domain: str, *, reference_domain: str) -> list[str]:
    """Return a list of warnings about input/reference domain mismatch.

    The bundled reference distributions are built from APOD display JPEGs,
    which carry 8x8 DCT artifacts, chroma subsampling, and 8-bit clipping.
    Linear/master-data inputs (FITS, 16-bit TIFF, 16-bit PNG) live in a
    fundamentally different domain, and several metrics — especially
    ``autocorr_width_px``, ``psd_high_band_suppression``, ``gradient_ratio``,
    and the color metrics — will read as anomalously good or bad simply because
    of the domain gap, not because of image quality.
    """
    if reference_domain != "display":
        return []
    if input_domain == "linear":
        return [
            "Domain mismatch: input is linear/master data (FITS or 16-bit raster) but the "
            "reference set is APOD display JPEGs. Noise, autocorrelation, gradient_ratio, "
            "and color metrics will be biased by the domain gap, not just by image quality. "
            "For comparable scoring, export an 8-bit JPEG/PNG with your final processing "
            "applied and submit that instead."
        ]
    if input_domain == "unknown":
        return [
            "Could not determine input domain from file extension; treat scores as "
            "approximate. Reference set is APOD display JPEGs."
        ]
    return []


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
