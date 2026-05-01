"""A8 — Reference archive pipeline.

Runs the A1–A6 measurement pipeline against an APOD archive in parallel,
caches per-image evaluation summaries (so changing one stage doesn't redo
the rest), categorizes targets via APOD title/explanation text, and builds
the per-category metric distributions that A7 scores against.

The full archive (~10,000 images) is too big to process in this session;
``build_archive_index`` and ``run_archive`` are designed to be run
incrementally — pass a ``limit`` for sampling, or schedule daily runs that
only process new entries.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .logging import get_logger

log = get_logger("apodornot.archive_pipeline")


# ---------------------------------------------------------------------------- #
# Target categorization (A8.3)
# ---------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CategoryRule:
    name: str
    keywords: tuple[str, ...]


# Order matters: more specific or higher-priority categories must win. We test
# in declaration order. Phenomena that often *appear in front of* deep-sky targets
# in APOD explanations (aurora over a Milky Way background, comet near a galaxy,
# planet near nebula) are checked before the deep-sky rules.
CATEGORY_RULES: tuple[CategoryRule, ...] = (
    CategoryRule(
        "aurora",
        ("aurora", "northern lights", "southern lights"),
    ),
    CategoryRule(
        "atmospheric",
        ("rainbow", "noctilucent clouds", "lightning", "sun pillar", "ice halo"),
    ),
    CategoryRule(
        "comet",
        ("comet ", "comet-", "tail of comet", "cometary"),
    ),
    CategoryRule(
        "solar_system",
        (
            "the moon", "lunar surface", "lunar eclipse", "moonrise", "moonset",
            "mars", "jupiter", "saturn", "venus", "mercury", "uranus", "neptune",
            "asteroid", "io,", "europa", "titan", "phobos", "ceres", "pluto",
            "the sun", "solar prominence", "solar flare", "solar eclipse",
            "international space station", "iss ", "iss,", "spacex", "falcon heavy",
            "starship", "rocket launch", "slim ", "spacecraft",
        ),
    ),
    CategoryRule(
        "planetary_nebula",
        ("planetary nebula", "helix nebula", "ring nebula", "dumbbell nebula", "ngc 7293"),
    ),
    CategoryRule(
        "supernova_remnant",
        ("supernova remnant", "veil nebula", "cygnus loop", "crab nebula"),
    ),
    # Per-target sub-categories of emission_nebula. These match BEFORE
    # emission_nebula so a Rosette/Orion/Horsehead image is scored against
    # same-target peers when enough samples exist.
    CategoryRule(
        "rosette",
        ("rosette", "ngc 2237", "ngc 2238", "ngc 2239", "ngc 2244", "ngc 2246"),
    ),
    CategoryRule(
        "orion_nebula",
        ("orion nebula", "m42", "m 42 ", "ngc 1976", "trapezium"),
    ),
    CategoryRule(
        "horsehead",
        ("horsehead", "ic 434", "barnard 33", "b33"),
    ),
    CategoryRule(
        "north_america_nebula",
        ("north america nebula", "ngc 7000", "pelican nebula", "ic 5070"),
    ),
    CategoryRule(
        "emission_nebula",
        (
            "emission nebula", "h ii region", "hii region", "h-alpha emission",
            "lagoon nebula", "trifid", "tarantula nebula", "california nebula",
            "seagull nebula", "heart nebula", "soul nebula", "elephant's trunk",
        ),
    ),
    CategoryRule(
        "reflection_nebula",
        ("reflection nebula", "iris nebula", "blue horsehead", "pleiades nebulosity"),
    ),
    CategoryRule(
        "dark_nebula",
        ("dark nebula", "barnard ", "molecular cloud", "ldn ", "b33 "),
    ),
    CategoryRule(
        "globular_cluster",
        ("globular cluster", "messier 13", "m13 ", "omega centauri", "47 tucanae", "m22 "),
    ),
    CategoryRule(
        "open_cluster",
        ("open cluster", "pleiades", "hyades", "double cluster", "beehive cluster"),
    ),
    CategoryRule(
        "galaxy_cluster",
        ("galaxy cluster", "abell ", "virgo cluster", "coma cluster"),
    ),
    CategoryRule(
        "galaxy",
        (
            "spiral galaxy", "elliptical galaxy", "irregular galaxy", "dwarf galaxy",
            "andromeda galaxy", "m31 ", "whirlpool galaxy", "pinwheel galaxy",
            "sombrero galaxy", "antennae galaxies", "galactic disk",
        ),
    ),
    CategoryRule(
        "milky_way_widefield",
        ("milky way", "rho ophiuchi", "galactic center", "galactic plane"),
    ),
)


def categorize_entry(title: str, explanation: str = "") -> str:
    """Classify an APOD entry by free-text matching against ``CATEGORY_RULES``.

    Returns ``"uncategorized"`` if no rule matches.
    """
    haystack = ((title or "") + " " + (explanation or "")).lower()
    for rule in CATEGORY_RULES:
        if any(re.search(rf"\b{re.escape(kw)}", haystack) for kw in rule.keywords):
            return rule.name
    return "uncategorized"


# ---------------------------------------------------------------------------- #
# Archive iteration / cache
# ---------------------------------------------------------------------------- #


@dataclass
class ArchiveEntry:
    image_path: Path
    sidecar_path: Path
    sidecar: dict[str, Any]
    category: str

    @property
    def date(self) -> str:
        return str(self.sidecar.get("date", self.image_path.stem))


def build_archive_index(archive_dir: str | Path) -> list[ArchiveEntry]:
    """Walk an APOD archive on disk and return all astrophoto entries.

    Skips entries whose sidecar classification is ``video`` or ``non_astrophoto``.
    """
    root = Path(archive_dir)
    out: list[ArchiveEntry] = []
    if not root.exists():
        return out
    for sidecar_path in sorted(root.rglob("*.json")):
        if sidecar_path.name.endswith(".video.json"):
            continue
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if sidecar.get("classification") not in (None, "astrophoto"):
            continue
        # The image path is the sidecar path without the `.json` suffix.
        image_path = sidecar_path.with_suffix("")
        if not image_path.exists():
            continue
        category = categorize_entry(sidecar.get("title", ""), sidecar.get("explanation", ""))
        out.append(
            ArchiveEntry(
                image_path=image_path,
                sidecar_path=sidecar_path,
                sidecar=sidecar,
                category=category,
            )
        )
    return out


# ---------------------------------------------------------------------------- #
# Per-image evaluation cache (A8.2)
# ---------------------------------------------------------------------------- #


def evaluation_cache_path(cache_dir: Path, image_path: Path) -> Path:
    return cache_dir / f"{image_path.stem}.eval.json"


def _evaluate_one_for_cache(
    image_path: str, cache_dir: str
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Worker — evaluate one image and write a JSON cache. Returns (path, summary, error)."""
    try:
        # Re-import inside the worker to avoid pickling heavy state.
        from .pipeline import evaluate_image

        cache_dir_p = Path(cache_dir)
        cache_dir_p.mkdir(parents=True, exist_ok=True)
        cache_path = evaluation_cache_path(cache_dir_p, Path(image_path))
        result = evaluate_image(image_path)
        summary = result.to_summary()
        cache_path.write_text(json.dumps(summary, default=_json_default))
        return image_path, summary, None
    except Exception as exc:  # noqa: BLE001 - return error to parent
        return image_path, None, f"{type(exc).__name__}: {exc}"


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


# ---------------------------------------------------------------------------- #
# Top-level archive runner (A8.1)
# ---------------------------------------------------------------------------- #


def run_archive(
    archive_dir: str | Path,
    cache_dir: str | Path,
    *,
    workers: int | None = None,
    limit: int | None = None,
    force: bool = False,
    progress: bool = True,
) -> dict[str, Any]:
    """Process every astrophoto in ``archive_dir``, caching per-image evaluations.

    Returns a status dict ``{"processed", "skipped", "errors", "by_category"}``.
    """
    archive_dir = Path(archive_dir)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    entries = build_archive_index(archive_dir)
    if limit:
        entries = entries[:limit]

    workers = workers or max(1, (os.cpu_count() or 1) - 1)
    log.info("Archive run: %d entries, %d workers", len(entries), workers)

    processed = 0
    skipped = 0
    errors: list[tuple[str, str]] = []
    by_category: dict[str, int] = {}

    todo: list[ArchiveEntry] = []
    for e in entries:
        cache_path = evaluation_cache_path(cache_dir, e.image_path)
        if not force and cache_path.exists():
            skipped += 1
            by_category[e.category] = by_category.get(e.category, 0) + 1
            continue
        todo.append(e)

    if todo and workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_evaluate_one_for_cache, str(e.image_path), str(cache_dir)): e
                for e in todo
            }
            for fut in as_completed(futures):
                entry = futures[fut]
                _, summary, err = fut.result()
                if err is not None:
                    errors.append((str(entry.image_path), err))
                    if progress:
                        log.warning("FAIL %s — %s", entry.image_path.name, err)
                    continue
                processed += 1
                by_category[entry.category] = by_category.get(entry.category, 0) + 1
                if progress:
                    log.info("OK %s [%s]", entry.image_path.name, entry.category)
    else:
        for e in todo:
            _, summary, err = _evaluate_one_for_cache(str(e.image_path), str(cache_dir))
            if err is not None:
                errors.append((str(e.image_path), err))
                if progress:
                    log.warning("FAIL %s — %s", e.image_path.name, err)
                continue
            processed += 1
            by_category[e.category] = by_category.get(e.category, 0) + 1
            if progress:
                log.info("OK %s [%s]", e.image_path.name, e.category)

    return {
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "by_category": by_category,
        "total_entries": len(entries),
    }


# ---------------------------------------------------------------------------- #
# A8.4 — Distribution building
# ---------------------------------------------------------------------------- #

# Metric paths inside the per-image summary that A7 scores against. Each entry
# is (metric_name, summary_path, higher_is_better). Summary paths use dot syntax.
SCORING_METRICS: tuple[tuple[str, str, bool], ...] = (
    # Star field
    ("median_fwhm_px",            "star_field.median_fwhm_px",            False),
    ("median_eccentricity",       "star_field.median_eccentricity",       False),
    ("fwhm_corner_excess",        "star_field.fwhm_corner_excess",        False),
    ("fwhm_quadrant_asymmetry",   "star_field.fwhm_quadrant_asymmetry",   False),
    # Noise
    ("noise_floor_l",             "noise.noise_floors.L",                  False),
    ("psd_spectral_slope",        "noise.psd_spectral_slope",              True),  # less negative = better
    ("psd_high_band_suppression", "noise.psd_high_band_suppression",       False),
    ("autocorr_width_px",         "noise.autocorr_width_px",               False),
    ("snr_target_median",         "noise.snr_target_median",               True),
    ("fpn_max_pattern",           "noise.fpn_max_pattern",                  False),
    # Target freq
    ("target_spectral_slope",     "target_freq.spectral_slope",            True),
    ("target_effective_resolution","target_freq.effective_resolution_cycles_per_px", True),
    # Calibration
    ("gradient_ratio",            "calibration.gradient_ratio",            False),
    ("vignetting_falloff",        "calibration.vignetting_falloff",        False),
    ("color_balance_magnitude",   "calibration.color_balance_magnitude",   False),
    # Color
    ("color_overall_score",       "color.overall_score",                   True),
    ("star_diversity_score",      "color.star_diversity_score",            True),
    ("background_chroma_distance","color.background_chroma_distance",      False),
    ("chroma_concentration",      "color.chroma_concentration",            False),
)


def _get_path(d: dict[str, Any], path: str) -> float | None:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    if isinstance(cur, (int, float)) and np.isfinite(cur):
        return float(cur)
    return None


@dataclass
class MetricDistribution:
    metric: str
    higher_is_better: bool
    n: int
    values: list[float] = field(default_factory=list)
    quantiles: dict[str, float] = field(default_factory=dict)

    def percentile(self, value: float) -> float:
        """Where ``value`` falls in this distribution, 0..100."""
        if not self.values:
            return float("nan")
        arr = np.asarray(self.values)
        rank = float((arr <= value).mean() * 100.0)
        return rank if self.higher_is_better else 100.0 - rank


@dataclass
class ArchiveDistributions:
    by_category: dict[str, dict[str, MetricDistribution]]

    def categories(self) -> Iterable[str]:
        return self.by_category.keys()

    def for_category(self, category: str) -> dict[str, MetricDistribution]:
        if category in self.by_category:
            return self.by_category[category]
        if "global" in self.by_category:
            return self.by_category["global"]
        return next(iter(self.by_category.values()), {})


def build_distributions(
    cache_dir: str | Path, archive_dir: str | Path
) -> ArchiveDistributions:
    """Aggregate cached evaluation summaries into per-category metric distributions."""
    cache_dir = Path(cache_dir)
    archive_dir = Path(archive_dir)

    entries = build_archive_index(archive_dir)
    by_category_data: dict[str, dict[str, list[float]]] = {}
    global_data: dict[str, list[float]] = {}

    for entry in entries:
        cache_path = evaluation_cache_path(cache_dir, entry.image_path)
        if not cache_path.exists():
            continue
        try:
            summary = json.loads(cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for metric_name, metric_path, _hib in SCORING_METRICS:
            v = _get_path(summary, metric_path)
            if v is None:
                continue
            global_data.setdefault(metric_name, []).append(v)
            cat = by_category_data.setdefault(entry.category, {})
            cat.setdefault(metric_name, []).append(v)

    def _to_dist(values: list[float], metric_name: str, hib: bool) -> MetricDistribution:
        arr = np.asarray(values)
        return MetricDistribution(
            metric=metric_name,
            higher_is_better=hib,
            n=int(len(values)),
            values=values,
            quantiles={
                "p10": float(np.percentile(arr, 10)),
                "p25": float(np.percentile(arr, 25)),
                "p50": float(np.percentile(arr, 50)),
                "p75": float(np.percentile(arr, 75)),
                "p90": float(np.percentile(arr, 90)),
            } if len(values) else {},
        )

    # Always include the "global" pool — A7 falls back to it when category has too few samples.
    by_category_data["global"] = global_data

    out: dict[str, dict[str, MetricDistribution]] = {}
    for category, metrics in by_category_data.items():
        out[category] = {}
        for metric_name, _path, hib in SCORING_METRICS:
            values = metrics.get(metric_name, [])
            out[category][metric_name] = _to_dist(values, metric_name, hib)
    return ArchiveDistributions(by_category=out)


def save_distributions(distributions: ArchiveDistributions, path: str | Path) -> None:
    """Serialize distributions to JSON for A7 to load."""
    payload: dict[str, Any] = {}
    for category, metrics in distributions.by_category.items():
        payload[category] = {
            name: {
                "metric": dist.metric,
                "higher_is_better": dist.higher_is_better,
                "n": dist.n,
                "values": dist.values,
                "quantiles": dist.quantiles,
            }
            for name, dist in metrics.items()
        }
    Path(path).write_text(json.dumps(payload))


def load_distributions(path: str | Path) -> ArchiveDistributions:
    payload = json.loads(Path(path).read_text())
    out: dict[str, dict[str, MetricDistribution]] = {}
    for category, metrics in payload.items():
        out[category] = {}
        for name, m in metrics.items():
            out[category][name] = MetricDistribution(
                metric=m["metric"],
                higher_is_better=m["higher_is_better"],
                n=m["n"],
                values=list(m["values"]),
                quantiles=m["quantiles"],
            )
    return ArchiveDistributions(by_category=out)


__all__ = [
    "ArchiveDistributions",
    "ArchiveEntry",
    "CATEGORY_RULES",
    "MetricDistribution",
    "SCORING_METRICS",
    "build_archive_index",
    "build_distributions",
    "categorize_entry",
    "evaluation_cache_path",
    "load_distributions",
    "run_archive",
    "save_distributions",
]
