"""Smoke tests for A9 — MCP server tool definitions.

These tests don't drive the MCP transport; they call the tool functions
directly so we can verify the tool surface and that each routes to a real
pipeline call without crashing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mcp")

from apodornot import mcp_server


APOD_FIXTURE = Path("apod_archive/2024/2024-01-15.jpg")


def test_mcp_server_module_exposes_expected_tools():
    # FastMCP records registered tools internally — exercise the public ones via
    # their wrapped Python callables.
    expected = [
        "evaluate_image_tool",
        "score_image_tool",
        "get_stage_detail",
        "list_reference_matches",
        "get_submission_history",
        "get_diagnostic_context",
    ]
    for name in expected:
        assert hasattr(mcp_server, name), f"missing tool: {name}"


def test_get_diagnostic_context_known_metric():
    out = mcp_server.get_diagnostic_context("median_fwhm_px", score=20.0)
    assert "what_it_measures" in out
    assert out["metric"] == "median_fwhm_px"
    assert out["score_supplied"] == 20.0


def test_get_diagnostic_context_unknown_returns_error():
    out = mcp_server.get_diagnostic_context("not_a_metric")
    assert "error" in out
    assert "available" in out


def test_get_submission_history_empty_user():
    out = mcp_server.get_submission_history("ghost-user")
    assert out["n_submissions"] == 0


def test_evaluate_image_tool_missing_file():
    out = mcp_server.evaluate_image_tool("/no/such/file.fits")
    assert "error" in out


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_evaluate_image_tool_real_image():
    # Reset the cache so we measure a real run, not a previous test's state.
    mcp_server._eval_cache.clear()
    out = mcp_server.evaluate_image_tool(str(APOD_FIXTURE))
    assert "image" in out
    assert "star_field" in out
    # The same image should hit the cache the second time.
    second = mcp_server.evaluate_image_tool(str(APOD_FIXTURE))
    assert second == out


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_score_image_tool_real_image_with_history():
    mcp_server._eval_cache.clear()
    mcp_server._score_cache.clear()
    mcp_server._history.clear()
    out = mcp_server.score_image_tool(str(APOD_FIXTURE), user_id="test-user")
    assert "overall_score" in out
    assert "axes" in out
    assert "diagnostics" in out
    history = mcp_server.get_submission_history("test-user")
    assert history["n_submissions"] == 1


@pytest.mark.skipif(not APOD_FIXTURE.exists(), reason="APOD fixture not downloaded")
def test_get_stage_detail_routes_aliases():
    mcp_server._eval_cache.clear()
    out = mcp_server.get_stage_detail(str(APOD_FIXTURE), "stars")
    assert out["stage"] == "star_field"
    out2 = mcp_server.get_stage_detail(str(APOD_FIXTURE), "frequency")
    assert out2["stage"] == "target_freq"


def test_list_reference_matches_uses_global_when_unknown_target():
    out = mcp_server.list_reference_matches(target_type="unknown_category", archive_dir="apod_archive")
    # When the requested type is unknown, we still list 'global' or filtered set.
    assert "n_total" in out
    assert "entries" in out
