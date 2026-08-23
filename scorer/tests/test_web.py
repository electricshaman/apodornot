"""Tests for the FastAPI streaming service (apodornot.web)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from apodornot.web import app, scorecard_to_dict


APOD_FIXTURE = Path("apod_archive/2024/2024-01-15.jpg")


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------- #
# Health
# ---------------------------------------------------------------------------- #


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------- #
# Evaluate streaming
# ---------------------------------------------------------------------------- #


def _parse_sse(stream_text: str) -> list[tuple[str, dict]]:
    """Parse a captured SSE stream into ``[(event_type, data_dict), ...]``."""
    events = []
    cur_event = None
    for line in stream_text.splitlines():
        if line.startswith("event: "):
            cur_event = line.removeprefix("event: ").strip()
        elif line.startswith("data: ") and cur_event is not None:
            data = json.loads(line.removeprefix("data: "))
            events.append((cur_event, data))
            cur_event = None
    return events


def test_evaluate_without_an_image_is_rejected(client):
    """/evaluate takes a multipart upload; omitting it is a validation error."""
    assert client.post("/evaluate").status_code == 422


def test_evaluate_reports_unreadable_image_as_a_stream_error(client):
    """An undecodable upload fails inside the SSE stream, not via the status code.

    The stream has already begun by the time the pipeline touches the bytes, so
    the failure is reported as ``event: error`` followed by ``event: done``.
    """
    r = client.post(
        "/evaluate", files={"image": ("x.jpg", b"not-an-image", "image/jpeg")}
    )
    assert r.status_code == 200
    types = [t for t, _ in _parse_sse(r.text)]
    assert "error" in types
    assert types[-1] == "done"


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_evaluate_streams_stage_events_then_scorecard(client):
    with client.stream(
        "POST", "/evaluate", files={"image": ("apod.jpg", APOD_FIXTURE.read_bytes(), "image/jpeg")}
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(chunk for chunk in r.iter_text())

    events = _parse_sse(body)
    types = [t for t, _ in events]

    # First event is the submission ID, last is done.
    assert types[0] == "submission"
    assert types[-1] == "done"

    # Each stage A1..A6 should produce running + done.
    stage_events = [(t, p) for t, p in events if t == "stage"]
    stages_seen = {p["stage"]: [] for _t, p in stage_events}
    for _t, p in stage_events:
        stages_seen[p["stage"]].append(p["status"])
    for stage in ("A1", "A2", "A3", "A4", "A5", "A6"):
        assert stage in stages_seen, f"missing {stage}"
        assert "running" in stages_seen[stage]
        assert "done" in stages_seen[stage]

    # Exactly one scorecard event with the expected fields.
    scorecards = [p for t, p in events if t == "scorecard"]
    assert len(scorecards) == 1
    sc = scorecards[0]
    assert "overall_score" in sc
    assert "axes" in sc and len(sc["axes"]) == 5
    assert "metrics" in sc
    assert "diagnostics" in sc
    assert sc["input_domain"] == "display"  # APOD JPEG


# ---------------------------------------------------------------------------- #
# scorecard_to_dict shape
# ---------------------------------------------------------------------------- #


def test_scorecard_to_dict_matches_design_doc_shape():
    """Quick structural check on the JSON shape we promise the LiveView."""
    from apodornot.scoring import AxisScore, MetricScore, ScoreCard

    sc = ScoreCard(
        image_path="x.jpg",
        target_category="rosette",
        reference_category="rosette",
        reference_n=33,
        axis_scores=[
            AxisScore(
                axis="Star quality",
                score=55.8,
                components=[
                    MetricScore(
                        metric="median_fwhm_px",
                        value=3.26,
                        rank_score=70.0,
                        higher_is_better=False,
                        raw_quantiles={"p50": 3.0},
                    )
                ],
            )
        ],
        metric_scores=[
            MetricScore(
                metric="median_fwhm_px",
                value=3.26,
                rank_score=70.0,
                higher_is_better=False,
                raw_quantiles={"p50": 3.0},
            )
        ],
        diagnostics=["finding 1"],
        overall_score=72.0,
        warnings=[],
        input_domain="display",
        reference_domain="display",
    )
    d = scorecard_to_dict(sc)
    # Top-level keys
    for k in (
        "image_path", "target_category", "reference_category", "reference_n",
        "input_domain", "reference_domain", "warnings", "overall_score",
        "axes", "metrics", "diagnostics",
    ):
        assert k in d
    # Round-trips through json
    json.dumps(d)


# ---------------------------------------------------------------------------- #
# Reference endpoint
# ---------------------------------------------------------------------------- #


def test_reference_endpoint_returns_entries(client, tmp_path):
    # Empty archive returns empty list, doesn't 500.
    r = client.get("/reference", params={"target_type": "global", "archive_dir": str(tmp_path)})
    assert r.status_code == 200
    assert r.json() == {"target_type": "global", "n": 0, "entries": []}


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_reference_endpoint_returns_real_entries(client):
    """Query the whole archive rather than one target type.

    Which categories exist depends on which APOD images happen to have been
    fetched, so asking for a specific one made this pass or fail on the
    contents of apod_archive rather than on the endpoint's behaviour.
    """
    r = client.get("/reference", params={"target_type": "global"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["target_type"] == "global"
    assert payload["n"] > 0
    assert payload["n"] == len(payload["entries"])
    assert all("title" in e and "date" in e for e in payload["entries"])
