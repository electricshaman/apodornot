"""A9 — MCP server exposing the apodornot measurement pipeline as tools.

The LLM never sees the raw image — only structured metrics returned by the
tools below. The conversation layer lives in whatever MCP client connects to
the server (Claude Desktop, Claude Code, custom UI, etc.).

Run as:

    apodornot-mcp                # stdio transport (Claude Desktop / Claude Code)
    apodornot-mcp --transport sse --port 8765   # for web clients
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .archive_pipeline import build_archive_index, categorize_entry
from .logging import configure_logging, get_logger
from .pipeline import EvaluationResult, evaluate_image
from .scoring import (
    load_default_distributions,
    render_radar_chart,
    score_evaluation,
)

log = get_logger("apodornot.mcp_server")

mcp = FastMCP("apodornot")


# ---------------------------------------------------------------------------- #
# A9.8 — In-process caches (per-image evaluation + scorecard)
# ---------------------------------------------------------------------------- #

_eval_cache: dict[str, EvaluationResult] = {}
_score_cache: dict[tuple[str, str], dict[str, Any]] = {}
_history: dict[str, list[dict[str, Any]]] = {}


def _cached_evaluate(image_path: str) -> EvaluationResult:
    if image_path not in _eval_cache:
        _eval_cache[image_path] = evaluate_image(image_path)
    return _eval_cache[image_path]


def _record_history(user: str, scorecard: dict[str, Any]) -> None:
    bucket = _history.setdefault(user, [])
    bucket.append({"timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z", **scorecard})


def _scorecard_to_dict(scorecard) -> dict[str, Any]:
    return {
        "image_path": scorecard.image_path,
        "target_category": scorecard.target_category,
        "reference_category": scorecard.reference_category,
        "reference_n": scorecard.reference_n,
        "input_domain": scorecard.input_domain,
        "reference_domain": scorecard.reference_domain,
        "warnings": scorecard.warnings,
        "overall_score": scorecard.overall_score,
        "axes": [
            {
                "axis": ax.axis,
                "score": ax.score,
                "components": [
                    {"metric": c.metric, "value": c.value, "percentile": c.percentile}
                    for c in ax.components
                ],
            }
            for ax in scorecard.axis_scores
        ],
        "metrics": [
            {
                "metric": m.metric,
                "value": m.value,
                "percentile": m.percentile,
                "higher_is_better": m.higher_is_better,
                "quantiles": m.raw_quantiles,
            }
            for m in scorecard.metric_scores
        ],
        "diagnostics": scorecard.diagnostics,
    }


# ---------------------------------------------------------------------------- #
# A9.2 — evaluate_image
# ---------------------------------------------------------------------------- #


@mcp.tool()
def evaluate_image_tool(image_path: str) -> dict[str, Any]:
    """Run the full A1-A6 measurement pipeline against a local image.

    Returns the structured metrics summary: image properties, source counts,
    star-field statistics, noise characterization, target frequency analysis,
    calibration assessment, and color analysis. The image is never loaded by
    the LLM — only the resulting metrics.

    Args:
        image_path: Absolute path to a FITS / TIFF / PNG / JPEG astrophoto.
    """
    p = Path(image_path)
    if not p.exists():
        return {"error": f"file not found: {image_path}"}
    result = _cached_evaluate(str(p))
    return result.to_summary()


# ---------------------------------------------------------------------------- #
# A9.3 — score_image
# ---------------------------------------------------------------------------- #


@mcp.tool()
def score_image_tool(
    image_path: str,
    target_type: str | None = None,
    radar_output_path: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Run the pipeline, then percentile-score the image vs the APOD reference set.

    Returns the full scorecard: per-axis scores (Star quality / Noise management /
    Detail resolution / Gradient control / Color calibration), per-metric
    percentiles, the reference category used, an overall 0-100 score, and a
    diagnostic findings list with actionable suggestions.

    Args:
        image_path: Absolute path to a local astrophoto.
        target_type: Optional category override ("emission_nebula", "galaxy",
            "planetary_nebula", etc.). Auto-detected if omitted.
        radar_output_path: Optional path to write a radar-chart PNG.
        user_id: Optional identifier to record this scorecard against for
            longitudinal tracking via get_submission_history.
    """
    p = Path(image_path)
    if not p.exists():
        return {"error": f"file not found: {image_path}"}
    cache_key = (str(p), target_type or "")
    if cache_key in _score_cache:
        result = _score_cache[cache_key]
    else:
        ev = _cached_evaluate(str(p))
        scorecard = score_evaluation(ev, target_type=target_type)
        result = _scorecard_to_dict(scorecard)
        _score_cache[cache_key] = result

    if radar_output_path:
        from .scoring import score_evaluation as _se
        ev = _cached_evaluate(str(p))
        sc = _se(ev, target_type=target_type)
        out = render_radar_chart(sc, radar_output_path)
        result = {**result, "radar_chart_path": str(out)}

    if user_id:
        _record_history(user_id, result)
    return result


# ---------------------------------------------------------------------------- #
# A9.4 — get_stage_detail
# ---------------------------------------------------------------------------- #


_STAGE_KEYS = {
    "image": "image",
    "sources": "sources",
    "star_field": "star_field",
    "stars": "star_field",
    "noise": "noise",
    "target_freq": "target_freq",
    "frequency": "target_freq",
    "calibration": "calibration",
    "gradient": "calibration",
    "color": "color",
}


@mcp.tool()
def get_stage_detail(image_path: str, stage: str) -> dict[str, Any]:
    """Return the deep-dive details for one stage of a previously evaluated image.

    Args:
        image_path: Same path used for evaluate_image_tool / score_image_tool.
        stage: One of "image", "sources", "star_field" (alias "stars"),
            "noise", "target_freq" (alias "frequency"), "calibration"
            (alias "gradient"), "color".
    """
    key = _STAGE_KEYS.get(stage.lower())
    if key is None:
        return {"error": f"unknown stage '{stage}'", "valid": list(_STAGE_KEYS.keys())}
    p = Path(image_path)
    if not p.exists():
        return {"error": f"file not found: {image_path}"}
    summary = _cached_evaluate(str(p)).to_summary()
    return {"stage": key, "detail": summary.get(key, {})}


# ---------------------------------------------------------------------------- #
# A9.5 — list_reference_matches
# ---------------------------------------------------------------------------- #


@mcp.tool()
def list_reference_matches(
    image_path: str | None = None,
    target_type: str | None = None,
    archive_dir: str = "apod_archive",
    limit: int = 20,
) -> dict[str, Any]:
    """List APOD entries that comprise the reference set used to score the image.

    Args:
        image_path: Optional — used to auto-detect target_type if not given.
        target_type: Optional explicit category.
        archive_dir: Local APOD archive directory (default: 'apod_archive').
        limit: Max number of entries to return.
    """
    if not target_type and image_path:
        title_guess = Path(image_path).stem
        target_type = categorize_entry(title_guess)
    target_type = (target_type or "global").lower()

    entries = build_archive_index(archive_dir)
    if target_type != "global":
        matched = [e for e in entries if e.category == target_type]
    else:
        matched = entries
    matched = matched[:limit]

    return {
        "target_type": target_type,
        "n_total": len(matched),
        "entries": [
            {
                "date": e.date,
                "title": e.sidecar.get("title"),
                "url": e.sidecar.get("url"),
                "hdurl": e.sidecar.get("hdurl"),
                "category": e.category,
            }
            for e in matched
        ],
    }


# ---------------------------------------------------------------------------- #
# A9.6 — get_submission_history
# ---------------------------------------------------------------------------- #


@mcp.tool()
def get_submission_history(user_id: str) -> dict[str, Any]:
    """Return the prior scorecards for ``user_id`` so the LLM can spot trends."""
    bucket = _history.get(user_id, [])
    return {"user_id": user_id, "n_submissions": len(bucket), "submissions": bucket}


# ---------------------------------------------------------------------------- #
# A9.7 — get_diagnostic_context
# ---------------------------------------------------------------------------- #


_DIAGNOSTIC_CONTEXT: dict[str, dict[str, Any]] = {
    "_reference_caveat": {
        "name": "About the reference set",
        "what_it_measures": (
            "The bundled reference distributions are built from APOD display "
            "images — overwhelmingly 8-bit JPEGs (some PNG, rare TIFF). They reflect "
            "the look of *finished, exported* astrophotos, not linear master data."
        ),
        "implications": [
            "Several metrics will be biased toward the JPEG fingerprint of the "
            "reference: autocorrelation width, high-frequency PSD suppression, "
            "gradient_ratio (in noise-floor units), and color-channel cross-correlations.",
            "Submitting a linear FITS / 16-bit TIFF will produce skewed scores "
            "because the input is in a different domain than the reference.",
            "For comparable scoring, export an 8-bit JPEG/PNG with your final "
            "processing applied and submit that.",
        ],
    },
    "median_fwhm_px": {
        "name": "Median FWHM",
        "what_it_measures": (
            "Full-width-at-half-maximum of the optical PSF in pixels. The most direct "
            "measure of how 'sharp' stars are."
        ),
        "common_causes_of_poor_score": [
            "Out-of-focus optics",
            "Atmospheric seeing",
            "Tracking error during exposure",
            "Excess optical aberrations or dirty optics",
        ],
        "what_to_do": [
            "Use an autofocuser or a Bahtinov mask",
            "Image during steady-air conditions / from a darker site",
            "Verify mount tracking and autoguiding",
        ],
    },
    "median_eccentricity": {
        "name": "Median eccentricity",
        "what_it_measures": (
            "How elongated stars are: 0 = round, ~1 = streaky. Pattern across the field "
            "diagnoses tracking vs. optical issues."
        ),
        "common_causes_of_poor_score": [
            "Periodic error / drift in RA tracking (uniform direction across field)",
            "Coma or field curvature (radial pattern)",
            "Sensor tilt (one corner worse than others)",
        ],
        "what_to_do": [
            "Improve autoguiding (PHD2 calibration, dither)",
            "Add a coma corrector or flattener",
            "Adjust sensor tilt",
        ],
    },
    "psd_high_band_suppression": {
        "name": "High-frequency suppression in background PSD",
        "what_it_measures": (
            "Fraction of mid-frequency power that's been suppressed in the high band — "
            "a fingerprint of aggressive luminance noise reduction."
        ),
        "common_causes_of_poor_score": [
            "Heavy NoiseXTerminator / Topaz / similar processing",
            "Heavy-handed wavelet noise reduction",
            "Median / bilateral filter applied late in the workflow",
        ],
        "what_to_do": [
            "Use lighter noise reduction settings",
            "Apply NR earlier (on linear data) and let the stretch reveal less of it",
            "Increase integration time so SNR is high enough to skip aggressive NR",
        ],
    },
    "autocorr_width_px": {
        "name": "Noise autocorrelation width",
        "what_it_measures": (
            "How many pixels wide the noise autocorrelation peak is. Ideal uncorrelated "
            "noise = 1 px; wider = pixel-level smearing from drizzle/resampling/NR."
        ),
        "common_causes_of_poor_score": [
            "Drizzle integration without enough sub-pixel diversity",
            "Bicubic / Lanczos resampling",
            "Strong noise reduction at the pixel scale",
        ],
        "what_to_do": [
            "Avoid drizzle when not justified by sampling",
            "Resample with care, or not at all",
            "Lighter NR",
        ],
    },
    "snr_target_median": {
        "name": "Target SNR (median)",
        "what_it_measures": "Median signal-to-noise ratio inside the segmented target.",
        "common_causes_of_poor_score": [
            "Insufficient integration time",
            "Heavy light pollution",
            "High sensor read noise / bad calibration",
        ],
        "what_to_do": [
            "Add subs (each doubling cuts noise by sqrt(2))",
            "Image from darker skies or use narrowband filters",
            "Verify dark/flat/bias calibration",
        ],
    },
    "gradient_ratio": {
        "name": "Sky gradient (vs. noise floor)",
        "what_it_measures": (
            "Peak-to-peak amplitude of a low-order polynomial fit to the background, "
            "normalized to the noise floor. >3x = noticeable, >10x = serious."
        ),
        "common_causes_of_poor_score": [
            "Light pollution gradient",
            "Moonglow",
            "Flat-field calibration not matching actual conditions",
        ],
        "what_to_do": [
            "Apply gradient removal in processing (DBE, GraXpert, etc.)",
            "Match flats to lights — same dust spots, same orientation",
            "Image at lower altitude relative to light pollution domes",
        ],
    },
    "vignetting_falloff": {
        "name": "Vignetting falloff",
        "what_it_measures": (
            "Radial brightness loss from center to corner of the frame. Real residual "
            "vignetting indicates incomplete flat-field correction."
        ),
        "common_causes_of_poor_score": [
            "Missing or stale flat frames",
            "Flats taken at a different focuser position",
            "Filter wheel / sensor tilt change since flats",
        ],
        "what_to_do": [
            "Recapture flats at the current setup",
            "Ensure flats and lights share dust spots and alignment",
        ],
    },
    "color_overall_score": {
        "name": "Overall color calibration",
        "what_it_measures": "Composite of star-color diversity, background neutrality, and palette confidence.",
        "common_causes_of_poor_score": [
            "Saturation pushed too high in processing",
            "Background not neutralized",
            "Narrowband palette miscalibrated",
        ],
        "what_to_do": [
            "Use background neutralization and color calibration tools",
            "Sample stars from a survey for white reference",
            "Mute saturation in narrowband palette mapping",
        ],
    },
    "background_chroma_distance": {
        "name": "Background chromaticity distance",
        "what_it_measures": "Chromaticity distance of the median background from neutral gray.",
        "common_causes_of_poor_score": [
            "Color cast from filter / sensor",
            "Skipped background neutralization step",
        ],
        "what_to_do": [
            "Background neutralization (PixInsight BN / GraXpert / similar)",
            "Use a g-band-balanced flat",
        ],
    },
}


@mcp.tool()
def get_diagnostic_context(metric_name: str, score: float | None = None) -> dict[str, Any]:
    """Return what a metric means and what to do about a poor score.

    Args:
        metric_name: One of the metric keys returned by score_image_tool
            (e.g. "median_fwhm_px", "gradient_ratio").
        score: Optional percentile (0-100) — included in the response for
            convenience but does not change the returned text.
    """
    if metric_name not in _DIAGNOSTIC_CONTEXT:
        return {"error": f"no diagnostic context for metric '{metric_name}'", "available": list(_DIAGNOSTIC_CONTEXT.keys())}
    ctx = dict(_DIAGNOSTIC_CONTEXT[metric_name])
    ctx["metric"] = metric_name
    if score is not None:
        ctx["score_supplied"] = float(score)
    return ctx


# ---------------------------------------------------------------------------- #
# A9.1 — Server entry
# ---------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(prog="apodornot-mcp", description="apodornot MCP server.")
    parser.add_argument("--transport", choices=("stdio", "sse"), default="stdio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()
    configure_logging(args.log_level)

    log.info("Starting apodornot MCP server (transport=%s)", args.transport)
    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run("sse", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
