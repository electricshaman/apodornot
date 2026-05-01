"""Unit tests for the APOD API client (A0)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from apodornot.apod_client import (
    ApodClient,
    ApodEntry,
    classify_entry,
    entry_paths,
    fetch_range,
    latest_archived_date,
)


# ---------------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------------- #


def _mock_response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError

        resp.raise_for_status.side_effect = HTTPError(f"HTTP {status_code}")
    return resp


def _mock_streaming_response(content: bytes):
    """Mimic ``requests.Session.get(stream=True)`` as a context manager."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.iter_content = MagicMock(return_value=[content])
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=resp)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _make_client(session, **kwargs):
    # Skip rate-limit sleep so tests stay fast.
    return ApodClient(api_key="TEST", min_request_interval=0.0, session=session, **kwargs)


# ---------------------------------------------------------------------------- #
# Classification
# ---------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "media_type,title,expected",
    [
        ("video", "Some video", "video"),
        ("image", "An artist's concept of a black hole", "non_astrophoto"),
        ("image", "Diagram of solar system", "non_astrophoto"),
        ("image", "M31: The Andromeda Galaxy", "astrophoto"),
        ("image", "All-sky map of the cosmic background", "non_astrophoto"),
    ],
)
def test_classify_entry(media_type, title, expected):
    e = ApodEntry(date="2024-01-01", title=title, explanation="", media_type=media_type)
    assert classify_entry(e) == expected


def test_apod_entry_best_image_url_prefers_hd():
    e = ApodEntry(
        date="2024-01-01",
        title="x",
        explanation="",
        media_type="image",
        url="http://x/lo.jpg",
        hdurl="http://x/hi.jpg",
    )
    assert e.best_image_url() == "http://x/hi.jpg"


def test_apod_entry_best_image_url_falls_back_to_url():
    e = ApodEntry(
        date="2024-01-01",
        title="x",
        explanation="",
        media_type="image",
        url="http://x/lo.jpg",
        hdurl=None,
    )
    assert e.best_image_url() == "http://x/lo.jpg"


def test_apod_entry_best_image_url_none_for_video():
    e = ApodEntry(date="2024-01-01", title="x", explanation="", media_type="video")
    assert e.best_image_url() is None


# ---------------------------------------------------------------------------- #
# Request layer
# ---------------------------------------------------------------------------- #


def test_get_by_date_returns_entry():
    payload = {
        "date": "2024-01-15",
        "title": "Test image",
        "explanation": "blah",
        "media_type": "image",
        "url": "http://example.com/img.jpg",
        "hdurl": "http://example.com/hd.jpg",
    }
    session = MagicMock()
    session.get.return_value = _mock_response(payload)
    client = _make_client(session)

    entry = client.get_by_date("2024-01-15")
    assert entry.date == "2024-01-15"
    assert entry.media_type == "image"
    assert entry.hdurl == "http://example.com/hd.jpg"


def test_get_range_returns_list():
    payload = [
        {"date": "2024-01-01", "title": "a", "explanation": "", "media_type": "image", "url": "u"},
        {"date": "2024-01-02", "title": "b", "explanation": "", "media_type": "image", "url": "u"},
    ]
    session = MagicMock()
    session.get.return_value = _mock_response(payload)
    client = _make_client(session)
    entries = client.get_range("2024-01-01", "2024-01-02")
    assert len(entries) == 2
    assert {e.date for e in entries} == {"2024-01-01", "2024-01-02"}


def test_request_retries_on_429(monkeypatch):
    session = MagicMock()
    session.get.side_effect = [
        _mock_response({}, status_code=429),
        _mock_response({"date": "2024-01-01", "title": "x", "explanation": "", "media_type": "image"}),
    ]
    monkeypatch.setattr("apodornot.apod_client.time.sleep", lambda _s: None)
    client = _make_client(session, max_retries=3)
    entry = client.get_by_date("2024-01-01")
    assert entry.date == "2024-01-01"
    assert session.get.call_count == 2


def test_request_gives_up_after_max_retries(monkeypatch):
    session = MagicMock()
    session.get.return_value = _mock_response({}, status_code=503)
    monkeypatch.setattr("apodornot.apod_client.time.sleep", lambda _s: None)
    client = _make_client(session, max_retries=2)
    with pytest.raises(RuntimeError):
        client.get_by_date("2024-01-01")
    assert session.get.call_count == 2


def test_download_image_writes_file(tmp_path):
    session = MagicMock()
    session.get.return_value = _mock_streaming_response(b"\x89PNG\r\n\x1a\n0123456789")
    client = _make_client(session)
    dest = tmp_path / "year" / "image.jpg"
    n = client.download_image("http://example.com/img.jpg", dest)
    assert dest.exists()
    assert dest.read_bytes() == b"\x89PNG\r\n\x1a\n0123456789"
    assert n == 18
    # No leftover .part file
    assert not dest.with_suffix(dest.suffix + ".part").exists()


# ---------------------------------------------------------------------------- #
# Path / sidecar plumbing
# ---------------------------------------------------------------------------- #


def test_entry_paths_uses_year_subdir():
    e = ApodEntry(
        date="2024-03-15",
        title="x",
        explanation="",
        media_type="image",
        hdurl="http://x/foo.tif?bar=1",
    )
    img, side = entry_paths(e, Path("/tmp/arc"))
    assert img == Path("/tmp/arc/2024/2024-03-15.tif")
    assert side == Path("/tmp/arc/2024/2024-03-15.tif.json")


def test_latest_archived_date_walks_sidecars(tmp_path):
    (tmp_path / "2023").mkdir()
    (tmp_path / "2024").mkdir()
    (tmp_path / "2023" / "2023-12-31.jpg.json").write_text("{}")
    (tmp_path / "2024" / "2024-02-10.jpg.json").write_text("{}")
    (tmp_path / "2024" / "2024-01-05.jpg.json").write_text("{}")
    assert latest_archived_date(tmp_path) == date(2024, 2, 10)


def test_latest_archived_date_returns_none_for_empty(tmp_path):
    assert latest_archived_date(tmp_path) is None


# ---------------------------------------------------------------------------- #
# fetch_range integration (mocked HTTP)
# ---------------------------------------------------------------------------- #


def test_fetch_range_writes_image_and_sidecar(tmp_path):
    session = MagicMock()
    api_payload = [
        {
            "date": "2024-01-01",
            "title": "Galaxy",
            "explanation": "M31",
            "media_type": "image",
            "url": "http://x/lo.jpg",
            "hdurl": "http://x/hd.jpg",
        }
    ]

    # First call: range query (json). Subsequent calls: image downloads (stream).
    session.get.side_effect = [
        _mock_response(api_payload),
        _mock_streaming_response(b"FAKEIMAGE"),
    ]
    client = _make_client(session)
    n = fetch_range(client, start="2024-01-01", end="2024-01-01", output_dir=tmp_path, incremental=False)
    assert n == 1

    img = tmp_path / "2024" / "2024-01-01.jpg"
    sidecar = tmp_path / "2024" / "2024-01-01.jpg.json"
    assert img.exists()
    assert sidecar.exists()
    side = json.loads(sidecar.read_text())
    assert side["title"] == "Galaxy"
    assert side["classification"] == "astrophoto"


def test_fetch_range_skips_videos_but_writes_sidecar(tmp_path):
    session = MagicMock()
    api_payload = [
        {
            "date": "2024-01-02",
            "title": "Cool video",
            "explanation": "",
            "media_type": "video",
            "url": "http://youtube.com/x",
        }
    ]
    session.get.side_effect = [_mock_response(api_payload)]
    client = _make_client(session)
    n = fetch_range(client, start="2024-01-02", end="2024-01-02", output_dir=tmp_path, incremental=False)
    assert n == 0
    sidecar = tmp_path / "2024" / "2024-01-02.video.json"
    assert sidecar.exists()
    assert json.loads(sidecar.read_text())["classification"] == "video"


def test_fetch_range_is_idempotent(tmp_path):
    """Calling fetch_range twice does not re-download already-archived images."""
    session = MagicMock()
    api_payload = [
        {
            "date": "2024-01-01",
            "title": "x",
            "explanation": "",
            "media_type": "image",
            "url": "http://x/lo.jpg",
        }
    ]
    session.get.side_effect = [
        _mock_response(api_payload),
        _mock_streaming_response(b"IMG"),
    ]
    client = _make_client(session)
    fetch_range(client, start="2024-01-01", end="2024-01-01", output_dir=tmp_path, incremental=False)

    # Second pass — only the metadata call, no image download. If the client
    # tries to download again, ``side_effect`` will be exhausted and raise.
    session.get.side_effect = [_mock_response(api_payload)]
    n = fetch_range(client, start="2024-01-01", end="2024-01-01", output_dir=tmp_path, incremental=False)
    assert n == 0


def test_fetch_range_incremental_skips_already_done(tmp_path):
    # Pre-seed an archive entry for 2024-01-01.
    year_dir = tmp_path / "2024"
    year_dir.mkdir()
    (year_dir / "2024-01-01.jpg").write_bytes(b"x")
    (year_dir / "2024-01-01.jpg.json").write_text(
        json.dumps(
            {
                "date": "2024-01-01",
                "title": "x",
                "explanation": "",
                "media_type": "image",
                "classification": "astrophoto",
            }
        )
    )

    session = MagicMock()
    api_payload = [
        {
            "date": "2024-01-02",
            "title": "y",
            "explanation": "",
            "media_type": "image",
            "url": "http://x/lo.jpg",
        }
    ]
    session.get.side_effect = [
        _mock_response(api_payload),
        _mock_streaming_response(b"IMG2"),
    ]
    client = _make_client(session)
    n = fetch_range(client, start="2024-01-01", end="2024-01-02", output_dir=tmp_path, incremental=True)
    assert n == 1
    assert (tmp_path / "2024" / "2024-01-02.jpg").exists()
