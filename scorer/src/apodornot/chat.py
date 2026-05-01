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

import json
from collections.abc import AsyncIterator
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
You are an expert astrophotographer reviewing a quality evaluation produced by
the apodornot pipeline. The user has submitted an image and received the
scorecard below. Use the structured metrics to give specific, actionable feedback
grounded in the actual scores — not generic advice.

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


def _system_prompt(scorecard: dict[str, Any], reference_entries: list[dict]) -> str:
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
    return SYSTEM_TEMPLATE.format(
        scorecard_json=json.dumps(scorecard, indent=2),
        reference_summary=ref_summary,
    )


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
    archive_dir: str = "apod_archive",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
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

    system = _system_prompt(scorecard, reference_entries)
    history = [dict(m) for m in messages]  # avoid mutating caller's list

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
