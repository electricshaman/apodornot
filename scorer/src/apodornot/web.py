"""HTTP service for the LiveView frontend.

Wraps ``evaluate_image`` and ``score_evaluation`` in a FastAPI service that
streams stage-by-stage progress events plus the final scorecard via Server-Sent
Events. Designed to run on 127.0.0.1 alongside the Phoenix server — there is
no auth and the endpoints expose local file paths, so do not bind to a public
interface.

Run:

    apodornot-web                                          # default port 8000
    uvicorn apodornot.web:app --host 127.0.0.1 --port 8000

SSE event types emitted by ``GET /evaluate``:

    submission   {submission_id}                           # first event
    stage        {stage, status, detail}                   # one per stage transition
    scorecard    {<full ScoreCard JSON>}                   # final result
    error        {type, message}                           # if anything raised
    done         {}                                        # always last; close connection

The Phoenix LiveView side opens this stream, broadcasts each event onto a
PubSub topic per submission, and the LiveView ``stream_inserts`` stage events
and resolves its ``assign_async`` when the ``scorecard`` event arrives.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .archive_pipeline import build_archive_index
from .logging import configure_logging, get_logger
from .pipeline import evaluate_image
from .scoring import ScoreCard, score_evaluation

log = get_logger("apodornot.web")


# ---------------------------------------------------------------------------- #
# Lifespan
# ---------------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    log.info("apodornot web service starting")
    yield
    log.info("apodornot web service stopping")


app = FastAPI(title="apodornot", lifespan=lifespan)


# ---------------------------------------------------------------------------- #
# Health
# ---------------------------------------------------------------------------- #


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------- #
# Streaming evaluation
# ---------------------------------------------------------------------------- #


@app.get("/evaluate")
async def evaluate(image_path: str, target_type: str | None = None) -> StreamingResponse:
    """Run A1–A6 + scoring on ``image_path``, streaming stage events via SSE."""
    p = Path(image_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"image not found: {image_path}")

    submission_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    def on_progress(stage: str, status: str, detail: str = "") -> None:
        # Called from the worker thread; bounce onto the event loop safely.
        loop.call_soon_threadsafe(
            queue.put_nowait,
            ("stage", {"stage": stage, "status": status, "detail": detail}),
        )

    async def run_pipeline() -> None:
        try:
            ev = await loop.run_in_executor(
                None, lambda: evaluate_image(str(p), on_progress=on_progress)
            )
            sc = await loop.run_in_executor(
                None, lambda: score_evaluation(ev, target_type=target_type)
            )
            await queue.put(("scorecard", scorecard_to_dict(sc)))
        except Exception as exc:
            log.exception("pipeline failed for %s", p)
            await queue.put(
                ("error", {"type": type(exc).__name__, "message": str(exc)})
            )
        finally:
            await queue.put(("done", {}))

    asyncio.create_task(run_pipeline())

    async def event_stream():
        yield _sse("submission", {"submission_id": submission_id})
        while True:
            event_type, payload = await queue.get()
            yield _sse(event_type, payload)
            if event_type == "done":
                return

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------- #
# Reference set lookup (for the LiveView reference-comparison page)
# ---------------------------------------------------------------------------- #


@app.get("/reference")
async def reference(target_type: str = "global", archive_dir: str = "apod_archive") -> dict[str, Any]:
    """Return the APOD entries comprising the named reference set."""
    entries = build_archive_index(archive_dir)
    if target_type != "global":
        entries = [e for e in entries if e.category == target_type]
    return {
        "target_type": target_type,
        "n": len(entries),
        "entries": [
            {
                "date": e.date,
                "title": e.sidecar.get("title"),
                "url": e.sidecar.get("url"),
                "hdurl": e.sidecar.get("hdurl"),
                "category": e.category,
            }
            for e in entries
        ],
    }


# ---------------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------------- #


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Format a Server-Sent Events frame."""
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def scorecard_to_dict(sc: ScoreCard) -> dict[str, Any]:
    """Match the JSON shape documented in docs/ui-design-prompt.md."""
    return {
        "image_path": sc.image_path,
        "target_category": sc.target_category,
        "reference_category": sc.reference_category,
        "reference_n": sc.reference_n,
        "input_domain": sc.input_domain,
        "reference_domain": sc.reference_domain,
        "warnings": sc.warnings,
        "overall_score": sc.overall_score,
        "axes": [
            {
                "axis": ax.axis,
                "score": ax.score,
                "components": [
                    {
                        "metric": c.metric,
                        "value": c.value,
                        "percentile": c.percentile,
                        "higher_is_better": c.higher_is_better,
                    }
                    for c in ax.components
                ],
            }
            for ax in sc.axis_scores
        ],
        "metrics": [
            {
                "metric": m.metric,
                "value": m.value,
                "percentile": m.percentile,
                "higher_is_better": m.higher_is_better,
                "quantiles": m.raw_quantiles,
            }
            for m in sc.metric_scores
        ],
        "diagnostics": sc.diagnostics,
    }


# ---------------------------------------------------------------------------- #
# Entry point
# ---------------------------------------------------------------------------- #


def main() -> int:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="apodornot-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    uvicorn.run(
        "apodornot.web:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["app", "evaluate", "main", "reference", "scorecard_to_dict"]
