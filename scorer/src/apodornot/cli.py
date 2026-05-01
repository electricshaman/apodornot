"""CLI entrypoint: ``apodornot``.

Subcommands are wired in as the corresponding pipeline stages land.
"""

from __future__ import annotations

import argparse
import sys

from .logging import configure_logging, get_logger

log = get_logger("apodornot.cli")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apodornot", description="Astrophotography quality evaluation.")
    p.add_argument("--log-level", default=None, help="DEBUG / INFO / WARNING / ERROR")
    sub = p.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Download APOD archive entries.")
    fetch.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    fetch.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    fetch.add_argument("--output", default="apod_archive", help="Output directory")
    fetch.add_argument("--api-key", default=None, help="NASA API key (env: NASA_API_KEY)")

    ftgt = sub.add_parser(
        "fetch-target",
        help="Walk the APOD archive and download every entry matching a title regex.",
    )
    ftgt.add_argument(
        "--pattern", required=True,
        help="Case-insensitive regex tested against title (and explanation if --include-explanation)",
    )
    ftgt.add_argument("--start", default="1995-06-16", help="Start date YYYY-MM-DD (default: APOD start)")
    ftgt.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    ftgt.add_argument("--output", default="apod_archive", help="Output directory")
    ftgt.add_argument("--api-key", default=None, help="NASA API key (env: NASA_API_KEY)")
    ftgt.add_argument(
        "--include-explanation", action="store_true",
        help="Also match against the explanation text (slower, more false positives)",
    )
    ftgt.add_argument(
        "--metadata-only", action="store_true",
        help="Don't download images, just print matching titles + URLs",
    )

    evaluate = sub.add_parser("evaluate", help="Run measurement pipeline on an image.")
    evaluate.add_argument("image", help="Path to image (FITS / TIFF / PNG / JPEG)")
    evaluate.add_argument("--json", action="store_true", help="Emit JSON metrics")

    score = sub.add_parser("score", help="Score an image against APOD reference distributions.")
    score.add_argument("image", help="Path to image")
    score.add_argument("--target-type", default=None, help="Optional target category")
    score.add_argument("--radar", default=None, help="Output PNG path for radar chart")

    build = sub.add_parser(
        "build-archive",
        help="Run the measurement pipeline against an APOD archive (cached).",
    )
    build.add_argument("--archive", default="apod_archive", help="APOD archive directory")
    build.add_argument("--cache", default="apod_cache", help="Per-image evaluation cache dir")
    build.add_argument("--workers", type=int, default=None, help="Parallel workers (default: cpu - 1)")
    build.add_argument("--limit", type=int, default=None, help="Limit number of images processed")
    build.add_argument("--force", action="store_true", help="Re-process cached entries")

    dists = sub.add_parser(
        "build-distributions",
        help="Aggregate cached evaluations into reference distributions JSON.",
    )
    dists.add_argument("--archive", default="apod_archive", help="APOD archive directory")
    dists.add_argument("--cache", default="apod_cache", help="Per-image evaluation cache dir")
    dists.add_argument(
        "--output", default="src/apodornot/data/reference_distributions.json",
        help="Output path for distributions JSON (default: bundled package data)",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging(args.log_level)

    if args.command == "fetch":
        from .apod_client import ApodClient, fetch_range

        client = ApodClient(api_key=args.api_key)
        n = fetch_range(client, start=args.start, end=args.end, output_dir=args.output)
        log.info("Fetched %d APOD entries to %s", n, args.output)
        return 0

    if args.command == "fetch-target":
        from .apod_client import ApodClient, fetch_target

        client = ApodClient(api_key=args.api_key)
        fields = ("title", "explanation") if args.include_explanation else ("title",)
        n, matched = fetch_target(
            client,
            pattern=args.pattern,
            start=args.start,
            end=args.end,
            output_dir=args.output,
            metadata_only=args.metadata_only,
            fields=fields,
        )
        log.info(
            "fetch-target: %d matched, %d downloaded (pattern=%r, fields=%s)",
            len(matched), n, args.pattern, fields,
        )
        for e in matched:
            print(f"  {e.date}  {e.title}")
        return 0

    if args.command == "evaluate":
        from .pipeline import evaluate_image
        result = evaluate_image(args.image)
        if args.json:
            import json
            print(json.dumps(result.to_summary(), indent=2, default=str))
        else:
            print(result.text_report())
        return 0

    if args.command == "score":
        from .pipeline import evaluate_image
        from .scoring import render_radar_chart, score_evaluation

        result = evaluate_image(args.image)
        scorecard = score_evaluation(result, target_type=args.target_type)
        print(scorecard.text_report())
        if args.radar:
            out = render_radar_chart(scorecard, args.radar)
            log.info("Radar chart written to %s", out)
        return 0

    if args.command == "build-archive":
        from .archive_pipeline import run_archive

        result = run_archive(
            args.archive,
            args.cache,
            workers=args.workers,
            limit=args.limit,
            force=args.force,
        )
        log.info(
            "Archive run: processed=%d skipped=%d errors=%d",
            result["processed"], result["skipped"], len(result["errors"]),
        )
        for path, err in result["errors"][:20]:
            log.warning("FAIL %s: %s", path, err)
        log.info("Categories: %s", result["by_category"])
        return 0

    if args.command == "build-distributions":
        from .archive_pipeline import build_distributions, save_distributions

        dists = build_distributions(args.cache, args.archive)
        save_distributions(dists, args.output)
        n_global = max(
            (m.n for m in dists.by_category.get("global", {}).values()),
            default=0,
        )
        log.info(
            "Wrote distributions to %s (categories=%d, global n=%d)",
            args.output, len(dists.by_category), n_global,
        )
        return 0

    log.error("Unknown command: %s", args.command)
    return 2


if __name__ == "__main__":
    sys.exit(main())
