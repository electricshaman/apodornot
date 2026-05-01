"""Top-level orchestrator: run A1–A6 on an image and produce a unified result.

A7 (scoring) and A8 (reference archive) consume ``EvaluationResult`` to compare
metrics across the APOD distribution. The CLI uses ``evaluate_image`` and
``score_image`` to drive these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .color import ColorResult, analyze_color
from .gradient import CalibrationAssessment, assess_calibration
from .image_chars import ImageCharacterization, characterize_image
from .logging import get_logger
from .noise import NoiseResult, characterize_noise
from .star_field import StarFieldResult, analyze_star_field
from .target_freq import TargetFreqResult, analyze_target_frequency

log = get_logger("apodornot.pipeline")


@dataclass
class EvaluationResult:
    image_path: str
    image_chars: ImageCharacterization
    star_field: StarFieldResult
    noise: NoiseResult
    target_freq: TargetFreqResult
    calibration: CalibrationAssessment
    color: ColorResult
    extras: dict[str, Any] = field(default_factory=dict)

    def to_summary(self) -> dict:
        """Compact, JSON-serializable summary — what A7 / A8 consume."""
        meta = self.image_chars.metadata
        return {
            "image": {
                "path": self.image_path,
                "width": meta.width,
                "height": meta.height,
                "bit_depth": meta.bit_depth,
                "is_color": meta.is_color,
                "dynamic_range_used": meta.dynamic_range_used,
                "clipped_low_fraction": meta.clipped_low_fraction,
                "clipped_high_fraction": meta.clipped_high_fraction,
            },
            "sources": {
                "n_total": self.image_chars.sources.total_detected(),
                "n_stars": int(len(self.image_chars.sources.stars)),
                "n_extended": int(len(self.image_chars.sources.extended)),
                "n_artifacts": int(len(self.image_chars.sources.artifacts)),
                "median_psf_size_px": self.image_chars.sources.median_psf_size,
            },
            "star_field": self.star_field.summary(),
            "noise": self.noise.summary(),
            "target_freq": self.target_freq.summary(),
            "calibration": self.calibration.summary(),
            "color": self.color.summary(),
        }

    def text_report(self) -> str:
        s = self.to_summary()
        lines = [
            f"apodornot evaluation — {self.image_path}",
            f"  image: {s['image']['width']}x{s['image']['height']}, "
            f"{s['image']['bit_depth']}-bit, "
            f"{'color' if s['image']['is_color'] else 'mono'}",
            f"  dynamic range used: {s['image']['dynamic_range_used']:.3f}",
            f"  clipped: low={s['image']['clipped_low_fraction']:.3f}, "
            f"high={s['image']['clipped_high_fraction']:.3f}",
            "",
            "Sources:",
            f"  detected: {s['sources']['n_total']} "
            f"(stars={s['sources']['n_stars']}, "
            f"extended={s['sources']['n_extended']}, "
            f"artifacts={s['sources']['n_artifacts']})",
            "",
            "Star field (A2):",
            f"  median FWHM: {s['star_field']['median_fwhm_px']}",
            f"  ecc: median={s['star_field']['median_eccentricity']}, "
            f"pattern={s['star_field']['eccentricity_pattern']}",
            "",
            "Noise (A3):",
            f"  noise floors: {s['noise']['noise_floors']}",
            f"  PSD slope: {s['noise']['psd_spectral_slope']}",
            f"  high-band suppression: {s['noise']['psd_high_band_suppression']}",
            f"  autocorr width (px): {s['noise']['autocorr_width_px']}",
            f"  target SNR (median / p90): "
            f"{s['noise']['snr_target_median']} / {s['noise']['snr_target_p90']}",
            "",
            "Target frequency (A4):",
            f"  spectral slope: {s['target_freq']['spectral_slope']}",
            f"  effective resolution: "
            f"{s['target_freq']['effective_resolution_cycles_per_px']}",
            f"  artifacts: {s['target_freq']['artifact_kinds']}",
            "",
            "Calibration (A5):",
            f"  gradient ratio: {s['calibration']['gradient_ratio']:.2f}",
            f"  vignetting falloff: {s['calibration']['vignetting_falloff']}",
            f"  color balance magnitude: {s['calibration']['color_balance_magnitude']}",
            "",
            "Color (A6):",
            f"  palette: {s['color']['target_palette']} "
            f"(confidence {s['color']['palette_confidence']})",
            f"  star color diversity: {s['color']['star_diversity_score']}",
            f"  bg chroma distance: {s['color']['background_chroma_distance']}",
            f"  overall score: {s['color']['overall_score']}",
        ]
        return "\n".join(lines)


def evaluate_image(path: str | Path, *, max_stars: int = 100) -> EvaluationResult:
    """Run A1–A6 against a single image."""
    log.info("evaluate_image: %s", path)
    chars = characterize_image(path)
    sf = analyze_star_field(chars, max_stars=max_stars)
    fwhm = sf.field_variation.median_fwhm if sf.field_variation else None
    noise = characterize_noise(chars)
    tf = analyze_target_frequency(chars, fwhm_px=fwhm)
    cal = assess_calibration(chars)
    col = analyze_color(chars, sf)
    return EvaluationResult(
        image_path=str(path),
        image_chars=chars,
        star_field=sf,
        noise=noise,
        target_freq=tf,
        calibration=cal,
        color=col,
    )


def score_image(path: str | Path, *, target_type: str | None = None):
    """A7 entrypoint — for now, runs evaluate_image and forwards to A7's scorer."""
    from .scoring import score_evaluation

    eval_result = evaluate_image(path)
    return score_evaluation(eval_result, target_type=target_type)


__all__ = ["EvaluationResult", "evaluate_image", "score_image"]
