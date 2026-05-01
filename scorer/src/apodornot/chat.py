"""Streaming chat against a scorecard, grounded in the apodornot tool surface.

Used by ``apodornot.web``'s ``POST /chat`` endpoint. The LLM is given the user's
scorecard as context and a small set of tools — currently
``get_diagnostic_context`` — to look up structured knowledge-base entries about
specific metrics. Tools share their implementation with ``apodornot.mcp_server``
so there is one canonical knowledge base.

The chat endpoint deliberately does NOT expose evaluate_image / score_image as
tools — by the time the user is chatting, those have already run; the scorecard
is the input.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from .archive_pipeline import build_archive_index
from .logging import get_logger
from .mcp_server import _DIAGNOSTIC_CONTEXT

log = get_logger("apodornot.chat")


# Default model — Sonnet 4.6 is the right balance for chat-with-grounded-context.
DEFAULT_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------- #
# System prompt
# ---------------------------------------------------------------------------- #


SYSTEM_TEMPLATE = """\
You are an expert astrophotographer reviewing the user's image. You have BOTH:

  1. The submitted image itself (attached on the first user message), and
  2. The apodornot pipeline's quantitative scorecard for it (below).

# The grounding rule (critical)

Every **recommendation** must be anchored to a poorly-scoring metric in the
scorecard. The image is for *observation* — for naming the specific physical
cause of a poor score that the metrics can detect but can't diagnose. The
image is **not** a basis for action on its own.

Why: general-purpose AI models are notoriously bad at evaluating
astrophotography because they pattern-match on aesthetics with no calibrated
internal standards. The whole point of this tool is to ground feedback in
measurement. If you make a recommendation that no metric supports, you are
re-introducing exactly the failure mode this tool exists to prevent.

Concretely:

  - You see HDR-decomposition crackle in the bright cavity of a nebula but
    the relevant frequency-domain metrics are all in good standing →
    **do not recommend a fix**. Note the observation as a separate caveat
    ("worth a visual check") and move on.
  - The autocorrelation width is at the 12th percentile (poor) and the image
    has a soft, smeared appearance in faint regions → **recommend the fix**,
    grounded in the metric. The image observation lets you be specific about
    *where* the smearing shows up, but the metric is what justifies
    recommending action.

When you make a visual claim, cite the metric that supports it whenever
possible ("Detail resolution is 95th percentile so the fine structure in the
cavity rim is real spatial information, not synthesized detail").

# How to structure a full review

When the user asks for a general review ("what do you think?", "how does this
look?"), respond in three sections:

  ## What's working
  Strengths grounded in metrics that scored well. Cite the percentile rank
  for each claim. Use the image to add specific texture: name what you can
  see that supports the metric story (e.g., "Star color diversity is 100th
  percentile — you've kept the blue-white O-types around NGC 2244 distinct
  from the warmer field stars").

  ## Where to spend the next hour of editing
  2–4 actionable items, in order of visible payoff. **Each item must name a
  specific metric that scored poorly** (cite the percentile), use the image
  to identify the most likely physical cause, and propose the specific lever
  to pull. If no metric scored poorly enough to act on, this section can be
  short or absent — say that honestly.

  ## Visual observations (optional)
  Things you see in the image that don't have a metric backing them. Frame
  these as notes, not recommendations. Be explicit that they're un-anchored
  ("the pipeline doesn't catch this — worth a visual check").

  ## One sentence
  A single-sentence verdict.

  ## TODO
  A markdown checkbox list (`- [ ]`) of every concrete action your review
  recommended, in priority order. One line per task, imperative voice
  ("Cull the worst 30% of subs by FWHM/ecc before integration"), each one
  cite-able to a metric or a visual observation you made above. The user
  copies this list into their session notes — it must be self-contained.

When the user asks a focused question, skip the section headings and answer
directly, but **still finish with a `## TODO` checkbox list** of the actions
your answer implies. Same grounding rule applies: if a recommendation appears
in the TODO list, it must be anchored to a metric earlier in the response.

# Score convention — read this carefully

Every ``percentile`` field in the scorecard is **already direction-corrected so
that 100 = best**, regardless of the metric's ``higher_is_better`` flag. A
metric at percentile=70 ranks **better than 70% of the reference set**, period.
Do **not** invert the interpretation for ``higher_is_better=false`` metrics.
The pipeline has already done that arithmetic for you.

  - ``median_fwhm_px`` (lower-is-better) at percentile=70 → the user's stars
    are sharper than 70% of the reference set. Good.
  - ``gradient_ratio`` (lower-is-better) at percentile=20 → the user's gradient
    is worse than 80% of the reference set. Bad.
  - ``snr_target_median`` (higher-is-better) at percentile=90 → the user's SNR
    is higher than 90% of the reference set. Good.

The same convention applies to the ``score`` field on each axis (0–100, where
100 = best) and the top-level ``overall_score``.

``higher_is_better`` is preserved in the JSON only so you can describe which
direction the *raw value* moves to improve — never to re-invert the percentile.

# Tone

Be honest. If a score is genuinely good, say so without padding. If a score is
poor, name the specific cause (tracking error vs coma, NR vs drizzle, etc.)
based on the diagnostic patterns in the data, and suggest a concrete remediation.

**Never use emojis.** This is a working tool for astrophotographers, not a
chat app. Plain typography only.

# Reference set caveat

The bundled APOD reference distributions are built from display-format JPEGs.
Several metrics (autocorr_width_px, psd_high_band_suppression, gradient_ratio,
color metrics) are biased by JPEG domain artifacts. If the ``input_domain`` is
``linear`` (FITS / 16-bit raster), surface this explicitly.

# Tools

Call ``get_diagnostic_context`` to look up what a specific metric means and how
to remediate poor scores. Prefer calling it when you would otherwise have to
guess about what a metric measures.

# The scorecard

```json
{scorecard_json}
```

# Reference set the user is being compared against
{reference_summary}
"""


def _system_prompt(
    scorecard: dict[str, Any],
    reference_entries: list[dict],
    equipment_context: str | None = None,
) -> str:
    if reference_entries:
        ref_lines = [
            f"  - {e.get('date', '?')}  {e.get('title', '')}"
            for e in reference_entries[:10]
        ]
        ref_summary = "\n" + "\n".join(ref_lines)
        if len(reference_entries) > 10:
            ref_summary += f"\n  ... and {len(reference_entries) - 10} more"
    else:
        ref_summary = " (none available)"

    base = SYSTEM_TEMPLATE.format(
        scorecard_json=json.dumps(scorecard, indent=2),
        reference_summary=ref_summary,
    )

    if equipment_context and equipment_context.strip():
        equip_block = (
            "\n\n# Equipment context (provided by the user)\n\n"
            "Use this to reason about whether a metric reading is plausible "
            "given the actual setup (e.g., a small refractor on a strain-wave "
            "mount won't have meaningful vignetting or tracking error). Trust "
            "the equipment over the metric when they conflict and the metric is "
            "known to be biased for the target type.\n\n"
            f"```\n{equipment_context.strip()}\n```"
        )
        base = base + equip_block

    return base


# ---------------------------------------------------------------------------- #
# Tool surface — shared with MCP server's diagnostic knowledge base
# ---------------------------------------------------------------------------- #


TOOLS = [
    {
        "name": "get_diagnostic_context",
        "description": (
            "Return a structured knowledge-base entry explaining what a metric "
            "measures, what commonly causes poor scores, and what the user can "
            "do to improve. Call this whenever you need to ground a claim about "
            "a specific metric, or when the user asks about a metric directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric_name": {
                    "type": "string",
                    "description": (
                        "The metric key as it appears in the scorecard, e.g. "
                        "'median_fwhm_px', 'gradient_ratio', 'autocorr_width_px', "
                        "'psd_high_band_suppression', 'color_overall_score'."
                    ),
                },
            },
            "required": ["metric_name"],
        },
    },
]


# Anthropic image API limits: 5 MB max per image (base64-encoded), and they
# recommend ~1568 px on the long edge for best quality at lowest cost.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_IMAGE_LONG_EDGE = 1568


def _image_content_block(image_path: str) -> dict[str, Any] | None:
    """Build an Anthropic ``image`` content block from a local file path.

    Downscales to <=1568px long edge and re-encodes as JPEG to keep the
    base64 payload under Anthropic's 5 MB cap. Returns None for FITS / TIFF
    or unreadable files (the chat falls back to scorecard-only).
    """
    p = Path(image_path)
    if not p.exists():
        log.warning("chat image not found at %s — falling back to scorecard-only", p)
        return None

    mime, _ = mimetypes.guess_type(str(p))
    if mime not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        log.info("chat image %s has unsupported mime %s — scorecard-only", p, mime)
        return None

    try:
        from PIL import Image
        import io

        with Image.open(p) as img:
            img = img.convert("RGB")
            w, h = img.size
            long_edge = max(w, h)
            if long_edge > _MAX_IMAGE_LONG_EDGE:
                scale = _MAX_IMAGE_LONG_EDGE / long_edge
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

            # Re-encode at quality 85 — well above Claude's perceptual threshold,
            # well below 5 MB for any reasonable input.
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            data = buf.getvalue()

            # If still too large (extremely rare at 1568px@q85), step quality down.
            for q in (75, 65, 55):
                if len(data) <= _MAX_IMAGE_BYTES:
                    break
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=q, optimize=True)
                data = buf.getvalue()

        if len(data) > _MAX_IMAGE_BYTES:
            log.warning("chat image still > 5MB after compression — skipping")
            return None

        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(data).decode("ascii"),
            },
        }
    except OSError as exc:
        log.warning("failed to load chat image %s: %s", p, exc)
        return None


def _execute_tool(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Local tool dispatch. Mirrors the MCP server's get_diagnostic_context."""
    if name == "get_diagnostic_context":
        metric = tool_input.get("metric_name", "")
        ctx = _DIAGNOSTIC_CONTEXT.get(metric)
        if ctx is None:
            return {
                "error": f"no diagnostic context for metric '{metric}'",
                "available_metrics": sorted(_DIAGNOSTIC_CONTEXT.keys()),
            }
        return {**ctx, "metric": metric}
    return {"error": f"unknown tool: {name}"}


# ---------------------------------------------------------------------------- #
# Streaming chat loop
# ---------------------------------------------------------------------------- #


async def stream_chat(
    *,
    scorecard: dict[str, Any],
    messages: list[dict[str, Any]],
    image_path: str | None = None,
    equipment_context: str | None = None,
    archive_dir: str = "apod_archive",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2048,
    api_key: str | None = None,
    client: AsyncAnthropic | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Run an Anthropic streaming chat with the apodornot tool surface available.

    Yields ``(event_type, payload)`` tuples:

      * ``token``     — assistant text chunk; ``payload["text"]`` is the delta
      * ``tool_use``  — Claude is calling a tool; ``payload`` has ``name``+``input``
      * ``done``      — final assistant turn completed normally
      * ``error``     — something went wrong; ``payload["message"]`` is human-readable
    """
    client = client or AsyncAnthropic(api_key=api_key)

    # Build the reference summary once for the system prompt.
    target_type = scorecard.get("reference_category", "global")
    try:
        entries = build_archive_index(archive_dir)
        if target_type != "global":
            entries = [e for e in entries if e.category == target_type]
        reference_entries = [
            {"date": e.date, "title": e.sidecar.get("title")} for e in entries
        ]
    except Exception as exc:  # noqa: BLE001
        log.warning("reference index lookup failed: %s", exc)
        reference_entries = []

    system = _system_prompt(scorecard, reference_entries, equipment_context)
    history = [dict(m) for m in messages]  # avoid mutating caller's list

    # Attach the image to the first user turn so Claude sees the actual photo,
    # not just the metrics. Only on the first turn; subsequent turns reference
    # it from conversation history.
    if image_path:
        image_block = _image_content_block(image_path)
        if image_block is not None:
            for i, msg in enumerate(history):
                if msg.get("role") == "user":
                    content = msg.get("content")
                    if isinstance(content, str):
                        msg["content"] = [image_block, {"type": "text", "text": content}]
                    elif isinstance(content, list):
                        msg["content"] = [image_block] + content
                    history[i] = msg
                    break

    # Tool-use loop: each turn streams text and may end with tool_use blocks.
    # If so, we execute the tools and run another turn.
    max_turns = 4
    for _turn in range(max_turns):
        try:
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=TOOLS,
                messages=history,
            ) as stream:
                async for event in stream:
                    if getattr(event, "type", None) == "text":
                        text = getattr(event, "text", "") or ""
                        if text:
                            yield ("token", {"text": text})
                    elif getattr(event, "type", None) == "content_block_stop":
                        block = getattr(event, "content_block", None)
                        if block is not None and getattr(block, "type", None) == "tool_use":
                            yield (
                                "tool_use",
                                {
                                    "name": getattr(block, "name", ""),
                                    "input": getattr(block, "input", {}) or {},
                                },
                            )
                final = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            log.exception("anthropic stream failed")
            yield ("error", {"message": str(exc), "type": type(exc).__name__})
            return

        # Append the assistant turn to the history, then check for tool calls.
        history.append({"role": "assistant", "content": final.content})

        tool_uses = [
            b for b in final.content if getattr(b, "type", None) == "tool_use"
        ]
        if not tool_uses:
            yield ("done", {})
            return

        # Execute tools and add tool_result blocks for the next turn.
        results = []
        for block in tool_uses:
            result = _execute_tool(
                getattr(block, "name", ""),
                getattr(block, "input", {}) or {},
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                }
            )
        history.append({"role": "user", "content": results})

    # Hit the turn cap without a clean stop — surface an error so the UI can
    # fall back rather than hang.
    yield ("error", {"message": f"chat exceeded {max_turns} tool-use turns"})


__all__ = ["DEFAULT_MODEL", "TOOLS", "stream_chat"]
