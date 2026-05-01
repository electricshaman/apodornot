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

    evaluate = sub.add_parser("evaluate", help="Run measurement pipeline on an image.")
    evaluate.add_argument("image", help="Path to image (FITS / TIFF / PNG / JPEG)")
    evaluate.add_argument("--json", action="store_true", help="Emit JSON metrics")

    score = sub.add_parser("score", help="Score an image against APOD reference distributions.")
    score.add_argument("image", help="Path to image")
    score.add_argument("--target-type", default=None, help="Optional target category")

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
        from .pipeline import score_image
        result = score_image(args.image, target_type=args.target_type)
        print(result.text_report())
        return 0

    log.error("Unknown command: %s", args.command)
    return 2


if __name__ == "__main__":
    sys.exit(main())
