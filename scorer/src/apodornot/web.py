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
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .archive_pipeline import build_archive_index
from .chat import stream_chat
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


@app.get("/image/{submission_id}")
async def image(submission_id: str):
    """Serve the uploaded image for a submission, by submission_id prefix.

    Phoenix's UploadController proxies through this so the score view's
    image preview keeps working across Phoenix redeploys (Phoenix's local
    /tmp gets wiped, but the pipeline's /data volume persists).
    """
    if "/" in submission_id or "\\" in submission_id or ".." in submission_id:
        raise HTTPException(status_code=400, detail="invalid submission_id")
    upload_dir = Path(os.environ.get("APODORNOT_UPLOAD_DIR", "/tmp/apodornot_uploads"))
    matches = sorted(upload_dir.glob(f"{submission_id}_*"))
    if not matches:
        raise HTTPException(status_code=404, detail="image not found for submission")
    path = matches[0]
    # Resolve to absolute then check it stays inside upload_dir
    if Path(upload_dir).resolve() not in path.resolve().parents:
        raise HTTPException(status_code=400, detail="invalid path")
    return FileResponse(path)


# ---------------------------------------------------------------------------- #
# Streaming evaluation
# ---------------------------------------------------------------------------- #


@app.post("/evaluate")
async def evaluate(
    image: UploadFile = File(...),
    target_type: str | None = Form(default=None),
    submission_id: str | None = Form(default=None),
) -> StreamingResponse:
    """Run A1–A6 + scoring on the uploaded ``image``, streaming events via SSE.

    The pipeline service stores the image in its own volume
    (``$APODORNOT_UPLOAD_DIR``) keyed by submission_id so that subsequent
    /chat calls can find it. Phoenix sends the bytes via multipart so the
    two machines don't need to share a filesystem.
    """
    submission_id = submission_id or str(uuid.uuid4())
    upload_dir = Path(os.environ.get("APODORNOT_UPLOAD_DIR", "/tmp/apodornot_uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(image.filename or "upload.bin").name
    p = upload_dir / f"{submission_id}_{safe_name}"
    contents = await image.read()
    p.write_bytes(contents)
    log.info("evaluate: stored %d bytes at %s", len(contents), p)

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
            log.info("evaluate_image returned; queueing A7 running")
            on_progress("A7", "running", "scoring against reference set")
            log.info("calling score_evaluation in executor")
            sc = await loop.run_in_executor(
                None, lambda: score_evaluation(ev, target_type=target_type)
            )
            log.info("score_evaluation returned; queueing A7 done")
            on_progress(
                "A7",
                "done",
                f"overall {sc.overall_score:.0f}/100, vs {sc.reference_category} (n={sc.reference_n})",
            )
            log.info("building scorecard payload")
            payload = scorecard_to_dict(sc, evaluation=ev)
            log.info("scorecard payload built (%d keys); putting on queue", len(payload))
            await queue.put(("scorecard", payload))
            log.info("scorecard event enqueued")
        except Exception as exc:
            log.exception("pipeline failed for %s", p)
            await queue.put(
                ("error", {"type": type(exc).__name__, "message": str(exc)})
            )
        finally:
            await queue.put(("done", {}))

    asyncio.create_task(run_pipeline())

    async def event_stream():
        # Phoenix needs the pipeline-side image_path to call /chat later
        # (chat reads the file off the pipeline's filesystem too).
        yield _sse(
          "submission",
          {"submission_id": submission_id, "image_path": str(p)},
        )
        while True:
            event_type, payload = await queue.get()
            yield _sse(event_type, payload)
            if event_type == "done":
                return

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------- #
# Chat (Anthropic + apodornot tool surface)
# ---------------------------------------------------------------------------- #


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str | list[dict[str, Any]]


class ChatRequest(BaseModel):
    scorecard: dict[str, Any]
    messages: list[ChatMessage]
    image_path: str | None = None
    equipment_context: str | None = None
    archive_dir: str = "apod_archive"


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Stream a Claude chat turn over the user's scorecard via SSE.

    Tool calls land as ``event: tool_use`` for UX (e.g. "Claude is checking
    your noise stats..."). Token deltas land as ``event: token``. The stream
    closes with ``event: done`` (or ``event: error``).
    """
    messages = [m.model_dump() for m in req.messages]

    async def event_stream():
        try:
            async for event_type, payload in stream_chat(
                scorecard=req.scorecard,
                messages=messages,
                image_path=req.image_path,
                equipment_context=req.equipment_context,
                archive_dir=req.archive_dir,
                max_tokens=2048,
            ):
                yield _sse(event_type, payload)
        except Exception as exc:  # safety net — stream_chat already handles most
            log.exception("chat stream crashed")
            yield _sse("error", {"message": str(exc), "type": type(exc).__name__})
        finally:
            yield _sse("close", {})

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


def _sanitize_json(obj: Any) -> Any:
    """Replace non-finite floats (NaN, ±Infinity) with None recursively.

    Python's ``json.dumps`` emits ``NaN`` / ``Infinity`` literals by default,
    which are valid Python but **not** valid JSON per RFC 8259. Strict parsers
    on the consumer side (e.g. Elixir's ``Jason``) reject those tokens, which
    silently drops the SSE event downstream. Several pipeline metrics legitimately
    return NaN for sparse/edge-case inputs (star_diversity on a target with
    near-monochromatic stars, etc.) so we sanitize before serializing.
    """
    if isinstance(obj, float):
        if obj != obj or obj == float("inf") or obj == float("-inf"):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json(v) for v in obj]
    return obj


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Format a Server-Sent Events frame."""
    safe = _sanitize_json(payload)
    return f"event: {event}\ndata: {json.dumps(safe, default=str, allow_nan=False)}\n\n"


def scorecard_to_dict(sc: ScoreCard, *, evaluation=None) -> dict[str, Any]:
    """Match the JSON shape documented in docs/ui-design-prompt.md.

    If ``evaluation`` is provided, the per-stage diagnostic data (per-star
    measurements, radial PSD curve, background heatmap, etc.) is included
    under the ``diagnostics`` key so the UI can drive its visualizations
    from real data instead of synthetic mockups.
    """
    payload = {
        "image_path": sc.image_path,
        "target_category": sc.target_category,
        "target_explicit": sc.target_explicit,
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
                        "rank_score": c.rank_score,
                        "percentile": c.rank_score,  # deprecated alias — see f1i
                        "higher_is_better": c.higher_is_better,
                        "label": c.label,
                        "unit": c.unit,
                        "format": c.format,
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
                "rank_score": m.rank_score,
                "percentile": m.rank_score,  # deprecated alias — see f1i
                "higher_is_better": m.higher_is_better,
                "quantiles": m.raw_quantiles,
                "label": m.label,
                "unit": m.unit,
                "format": m.format,
            }
            for m in sc.metric_scores
        ],
        "diagnostics": sc.diagnostics,
    }
    if evaluation is not None:
        payload["stage_diagnostics"] = evaluation.to_summary().get("diagnostics", {})
    return payload


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
