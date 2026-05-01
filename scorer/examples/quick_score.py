"""Example: programmatically evaluate and score one image, render a radar chart.

    python examples/quick_score.py path/to/your/image.fits
"""

from __future__ import annotations

import sys
from pathlib import Path

from apodornot.logging import configure_logging
from apodornot.pipeline import evaluate_image
from apodornot.scoring import render_radar_chart, score_evaluation


def main(args: list[str]) -> int:
    if len(args) != 1:
        print(__doc__)
        return 2

    configure_logging()
    image_path = Path(args[0])

    evaluation = evaluate_image(image_path)
    scorecard = score_evaluation(evaluation)
    print(scorecard.text_report())

    radar_path = image_path.with_suffix(".radar.png")
    render_radar_chart(scorecard, radar_path)
    print(f"\nradar chart -> {radar_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
