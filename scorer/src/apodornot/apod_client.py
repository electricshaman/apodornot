"""NASA APOD API client.

Implements A0 of the spec: rate-limited request layer, batch archive download
with JSON metadata sidecars, video skipping, and resumable incremental updates.

Get a free API key at https://api.data.nasa.gov/. ``DEMO_KEY`` works for small
runs (30 req/hour, 50 req/day). A real key is 1000 req/hour.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .logging import get_logger

log = get_logger("apodornot.apod_client")

APOD_ENDPOINT = "https://api.nasa.gov/planetary/apod"
APOD_FIRST_DATE = date(1995, 6, 16)


# ---------------------------------------------------------------------------- #
# Data model
# ---------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ApodEntry:
    """A single APOD response, normalized."""

    date: str
    title: str
    explanation: str
    media_type: str
    url: str | None = None
    hdurl: str | None = None
    copyright: str | None = None
    thumbnail_url: str | None = None
    service_version: str | None = None

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> ApodEntry:
        return cls(
            date=payload["date"],
            title=payload.get("title", ""),
            explanation=payload.get("explanation", ""),
            media_type=payload.get("media_type", "unknown"),
            url=payload.get("url"),
            hdurl=payload.get("hdurl"),
            copyright=payload.get("copyright"),
            thumbnail_url=payload.get("thumbnail_url"),
            service_version=payload.get("service_version"),
        )

    def best_image_url(self) -> str | None:
        """Return the best URL for the image, preferring HD where available."""
        if self.media_type != "image":
            return None
        return self.hdurl or self.url


# ---------------------------------------------------------------------------- #
# A0.1 — Request layer
# ---------------------------------------------------------------------------- #


class ApodClient:
    """Thin wrapper around the APOD REST API.

    Adds a fixed inter-request delay (rate limiting), exponential backoff for
    429 / 5xx responses, and a shared ``requests.Session`` for connection reuse.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        min_request_interval: float = 0.6,
        max_retries: int = 5,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("NASA_API_KEY") or "DEMO_KEY"
        self.min_request_interval = min_request_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._session = session or requests.Session()
        self._last_request_at: float = 0.0

        if self.api_key == "DEMO_KEY":
            log.warning(
                "Using DEMO_KEY — limited to 30 requests/hour, 50/day. "
                "Set NASA_API_KEY for production use."
            )

    # ---- public ---------------------------------------------------------- #

    def get_by_date(self, when: date | str) -> ApodEntry:
        """Fetch one APOD entry for a single date."""
        params = {"date": _to_date_str(when), "thumbs": "true"}
        payload = self._request(params)
        return ApodEntry.from_response(payload)

    def get_range(self, start: date | str, end: date | str) -> list[ApodEntry]:
        """Fetch a closed date range in a single API call."""
        params = {
            "start_date": _to_date_str(start),
            "end_date": _to_date_str(end),
            "thumbs": "true",
        }
        payload = self._request(params)
        if not isinstance(payload, list):
            payload = [payload]
        return [ApodEntry.from_response(item) for item in payload]

    def download_image(self, url: str, dest: Path) -> int:
        """Stream an image to disk. Returns bytes written."""
        self._respect_rate_limit()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        bytes_written = 0
        with self._session.get(url, stream=True, timeout=self.timeout) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)
        tmp.replace(dest)
        self._last_request_at = time.monotonic()
        return bytes_written

    # ---- internals ------------------------------------------------------- #

    def _respect_rate_limit(self) -> None:
        delta = time.monotonic() - self._last_request_at
        if delta < self.min_request_interval:
            time.sleep(self.min_request_interval - delta)

    def _request(self, params: dict[str, Any]) -> Any:
        params = {**params, "api_key": self.api_key}
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            self._respect_rate_limit()
            try:
                resp = self._session.get(APOD_ENDPOINT, params=params, timeout=self.timeout)
                self._last_request_at = time.monotonic()
                if resp.status_code == 429 or resp.status_code >= 500:
                    backoff = min(60.0, 2.0**attempt)
                    log.warning(
                        "APOD %s on attempt %d, sleeping %.1fs", resp.status_code, attempt + 1, backoff
                    )
                    time.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_err = exc
                backoff = min(60.0, 2.0**attempt)
                log.warning("APOD request failed (%s), sleeping %.1fs", exc, backoff)
                time.sleep(backoff)
        raise RuntimeError(f"APOD request failed after {self.max_retries} retries") from last_err


# ---------------------------------------------------------------------------- #
# A0.2 / A0.3 — Batch download with sidecars
# A0.4 — Video skipping / non-astrophoto flagging
# A0.5 — Resilience + incremental updates
# ---------------------------------------------------------------------------- #


# Heuristics to flag entries that probably aren't astrophotos. These are *flags*,
# not skips — A8 will use them during reference-distribution building.
_NON_ASTROPHOTO_HINTS = (
    "illustration",
    "illustrative",
    "diagram",
    "artist",
    "artist's concept",
    "artist concept",
    "painting",
    "drawing",
    "rendering",
    "render of",
    "animation",
    "animated",
    "computer simulation",
    "simulation of",
    "schematic",
    "infographic",
    "all-sky map",
    "sky chart",
)


def classify_entry(entry: ApodEntry) -> str:
    """Return a coarse classification label for filtering / categorization.

    One of: ``"video"``, ``"non_astrophoto"``, ``"astrophoto"``.
    """
    if entry.media_type == "video":
        return "video"
    haystack = (entry.title + " " + entry.explanation).lower()
    if any(hint in haystack for hint in _NON_ASTROPHOTO_HINTS):
        return "non_astrophoto"
    return "astrophoto"


def _ext_for_url(url: str) -> str:
    """Pick a sensible file extension based on the URL."""
    path = url.split("?", 1)[0]
    for suffix in (".fits", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".gif", ".webp"):
        if path.lower().endswith(suffix):
            return suffix
    return ".jpg"


def _to_date_str(d: date | str) -> str:
    if isinstance(d, date):
        return d.isoformat()
    # Validate string form
    datetime.strptime(d, "%Y-%m-%d")
    return d


def _date_chunks(start: date, end: date, chunk_days: int = 30) -> Iterator[tuple[date, date]]:
    """Yield (start, end) date pairs covering [start, end] in <= chunk_days slices."""
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk_days - 1), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def entry_paths(entry: ApodEntry, root: Path, image_url: str | None = None) -> tuple[Path, Path]:
    """Return ``(image_path, sidecar_path)`` for an entry under ``root``.

    Sidecar lives next to the image with the same stem and a ``.json`` suffix.
    """
    year = entry.date.split("-", 1)[0]
    url = image_url or entry.best_image_url() or ""
    ext = _ext_for_url(url) if url else ".jpg"
    image_path = root / year / f"{entry.date}{ext}"
    sidecar_path = image_path.with_suffix(image_path.suffix + ".json")
    return image_path, sidecar_path


def write_sidecar(entry: ApodEntry, sidecar_path: Path, *, classification: str | None = None) -> None:
    """Write ``entry`` (plus classification + ingest timestamp) to JSON."""
    payload = asdict(entry)
    payload["classification"] = classification or classify_entry(entry)
    payload["ingested_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def latest_archived_date(root: Path) -> date | None:
    """Return the most recent APOD date already archived under ``root``, or None."""
    if not root.exists():
        return None
    latest: date | None = None
    for sidecar in root.rglob("*.json"):
        # Sidecar names look like "YYYY-MM-DD.<ext>.json"
        stem = sidecar.name.split(".", 1)[0]
        try:
            d = date.fromisoformat(stem)
        except ValueError:
            continue
        if latest is None or d > latest:
            latest = d
    return latest


def fetch_range(
    client: ApodClient,
    *,
    start: date | str,
    end: date | str,
    output_dir: str | Path,
    skip_videos: bool = True,
    incremental: bool = True,
) -> int:
    """Download every APOD entry in ``[start, end]`` to ``output_dir``.

    Idempotent: skips any entry whose image *and* sidecar already exist on disk.
    Returns the number of new image files written.
    """
    root = Path(output_dir)
    start_d = date.fromisoformat(_to_date_str(start))
    end_d = date.fromisoformat(_to_date_str(end))
    if start_d > end_d:
        raise ValueError(f"start ({start_d}) is after end ({end_d})")

    if incremental:
        latest = latest_archived_date(root)
        if latest is not None and latest >= start_d:
            new_start = latest + timedelta(days=1)
            if new_start > end_d:
                log.info("Archive already covers %s..%s — nothing to do.", start_d, end_d)
                return 0
            log.info("Incremental: resuming from %s (latest archived = %s)", new_start, latest)
            start_d = new_start

    written = 0
    for chunk_start, chunk_end in _date_chunks(start_d, end_d, chunk_days=30):
        log.info("Fetching APOD %s..%s", chunk_start, chunk_end)
        entries = client.get_range(chunk_start, chunk_end)
        for entry in entries:
            try:
                if _ingest_entry(client, entry, root, skip_videos=skip_videos):
                    written += 1
            except Exception as exc:
                log.error("Failed to ingest %s (%s): %s", entry.date, entry.title, exc)
                continue
    return written


def _ingest_entry(
    client: ApodClient,
    entry: ApodEntry,
    root: Path,
    *,
    skip_videos: bool,
) -> bool:
    """Materialize one entry on disk. Returns True if a new image was downloaded."""
    classification = classify_entry(entry)

    if entry.media_type == "video" and skip_videos:
        # Still write a sidecar so incremental scans see the date.
        sidecar_path = root / entry.date.split("-", 1)[0] / f"{entry.date}.video.json"
        if not sidecar_path.exists():
            write_sidecar(entry, sidecar_path, classification="video")
            log.info("Skipped video: %s — %s", entry.date, entry.title)
        return False

    url = entry.best_image_url()
    if url is None:
        log.warning("No downloadable image for %s (media_type=%s)", entry.date, entry.media_type)
        return False

    image_path, sidecar_path = entry_paths(entry, root, image_url=url)
    if image_path.exists() and sidecar_path.exists():
        return False

    if not image_path.exists():
        log.info("Downloading %s — %s", entry.date, entry.title)
        client.download_image(url, image_path)
    write_sidecar(entry, sidecar_path, classification=classification)
    return True


__all__ = [
    "APOD_ENDPOINT",
    "APOD_FIRST_DATE",
    "ApodClient",
    "ApodEntry",
    "classify_entry",
    "entry_paths",
    "fetch_range",
    "latest_archived_date",
    "write_sidecar",
]
