"""Haiku-based extraction of structured acquisition metadata from free-text.

The user's "additional context" textarea is intentionally free-form (no UI
friction). To get structured fields the deterministic side can reason about
(effective integration time, per-filter sub counts + pass rates, processing
chain) we ship the text to Claude Haiku 4.5 — fast, cheap, JSON-only.

Cost: ~500 input + ~200 output tokens per extraction. ~$0.0008 per submission.
Cached by hash so the same context never extracts twice.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from anthropic import Anthropic

from .logging import get_logger

log = get_logger("apodornot.acquisition")

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


SYSTEM_PROMPT = """\
Extract structured acquisition metadata from astrophotography session notes.
Return ONLY valid JSON matching this exact schema. Use null for any field you
can't infer with high confidence — never guess.

{
  "telescope": string|null,
  "camera": string|null,
  "mount": string|null,
  "total_integration_min": number|null,
  "filters": [
    {
      "name": string,
      "n_subs": number|null,
      "exposure_s": number|null,
      "pass_rate": number|null
    }
  ],
  "processing_chain": [string],
  "site_class": "bortle_1_2" | "bortle_3_4" | "bortle_5+" | null,
  "notes": string|null
}

Rules:
- total_integration_min is the effective (post-rejection) integration time when
  it can be determined; otherwise total exposure regardless of rejection.
- filters[].pass_rate is in [0,1] (e.g. 0.30 for "30% kept"). Decimal not percent.
- processing_chain lists processing tools/steps in order: ["WBPP", "BlurX",
  "NoiseX", "DBE", ...]. Use canonical short names. Empty list if none mentioned.
- site_class — only set if Bortle/SQM/site quality is explicit; otherwise null.
- notes — anything important that doesn't fit the schema. Brief.
"""


# Tiny in-process cache. The same equipment_context hash maps to the same
# extracted JSON for the lifetime of the service, so repeated chats over the
# same submission don't re-call Anthropic.
_CACHE: dict[str, dict[str, Any]] = {}


def _key(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def extract(
    equipment_context: str,
    *,
    api_key: str | None = None,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any] | None:
    """Extract structured acquisition metadata. Returns None on empty input or
    extraction failure (caller falls back to raw text)."""
    if not equipment_context or not equipment_context.strip():
        return None

    key = _key(equipment_context)
    if key in _CACHE:
        return _CACHE[key]

    try:
        client = client or Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": equipment_context.strip()}],
        )
        text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
        # Haiku sometimes wraps JSON in markdown fences — strip them.
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        parsed = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("acquisition extract failed: %s", exc)
        return None

    _CACHE[key] = parsed
    return parsed


def format_for_prompt(extracted: dict[str, Any] | None) -> str:
    """Render extracted JSON as a compact prompt block, or return empty string."""
    if not extracted:
        return ""
    return json.dumps(extracted, indent=2)


__all__ = ["DEFAULT_MODEL", "extract", "format_for_prompt"]
