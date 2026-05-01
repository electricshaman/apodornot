defmodule ApodornotWebWeb.ScoreLiveTest do
  use ApodornotWebWeb.ConnCase
  import Phoenix.LiveViewTest

  alias ApodornotWeb.PipelineRunner
  alias Phoenix.PubSub

  @pubsub ApodornotWeb.PubSub

  test "score page renders the loading view first", %{conn: conn} do
    {:ok, view, html} = live(conn, ~p"/s/test-sub-1?image=test.jpg")
    assert html =~ "pipeline"
    assert html =~ "A1 → A6"
    refute html =~ "Findings"
  end

  test "stage events appear in the stream as they arrive", %{conn: conn} do
    sub_id = "test-sub-2"
    {:ok, view, _html} = live(conn, ~p"/s/#{sub_id}?image=test.jpg")

    PubSub.broadcast(@pubsub, PipelineRunner.topic(sub_id),
      {"stage", %{"stage" => "A1", "status" => "running", "detail" => "characterizing image"}})
    rendered = render(view)
    assert rendered =~ "A1"
    assert rendered =~ "running"
    assert rendered =~ "characterizing image"

    PubSub.broadcast(@pubsub, PipelineRunner.topic(sub_id),
      {"stage", %{"stage" => "A1", "status" => "done", "detail" => "1046 sources"}})
    rendered = render(view)
    assert rendered =~ "1046 sources"
  end

  test "scorecard event swaps the loading view for the scorecard", %{conn: conn} do
    sub_id = "test-sub-3"
    {:ok, view, _html} = live(conn, ~p"/s/#{sub_id}?image=rose.jpg")

    sc = %{
      "image_path" => "rose.jpg",
      "target_category" => "rosette",
      "reference_category" => "rosette",
      "reference_n" => 33,
      "input_domain" => "display",
      "reference_domain" => "display",
      "warnings" => [],
      "overall_score" => 72.0,
      "axes" => [
        %{"axis" => "Star quality", "score" => 55.8, "components" => [
          %{"metric" => "median_fwhm_px", "value" => 3.26, "percentile" => 70.0,
            "higher_is_better" => false}
        ]}
      ],
      "metrics" => [],
      "diagnostics" => ["Star eccentricity is elevated."]
    }

    PubSub.broadcast(@pubsub, PipelineRunner.topic(sub_id), {"scorecard", sc})
    rendered = render(view)
    assert rendered =~ "72"  # overall score
    assert rendered =~ "Star quality"
    assert rendered =~ "Star eccentricity is elevated"
    assert rendered =~ "rosette"
    refute rendered =~ "pipeline" |> String.replace(~r/\s+/, " ")
  end

  test "error event renders the failure panel", %{conn: conn} do
    sub_id = "test-sub-4"
    {:ok, view, _html} = live(conn, ~p"/s/#{sub_id}")

    PubSub.broadcast(@pubsub, PipelineRunner.topic(sub_id),
      {"error", %{"type" => "ReqError", "message" => "connection refused"}})
    rendered = render(view)
    assert rendered =~ "pipeline error"
    assert rendered =~ "connection refused"
  end

  test "domain warning banner shows when the scorecard has warnings", %{conn: conn} do
    sub_id = "test-sub-5"
    {:ok, view, _html} = live(conn, ~p"/s/#{sub_id}")

    sc = %{
      "image_path" => "x.fits",
      "target_category" => "rosette",
      "reference_category" => "rosette",
      "reference_n" => 33,
      "input_domain" => "linear",
      "reference_domain" => "display",
      "warnings" => ["Domain mismatch: input is linear/master data..."],
      "overall_score" => 82.0,
      "axes" => [],
      "metrics" => [],
      "diagnostics" => []
    }

    PubSub.broadcast(@pubsub, PipelineRunner.topic(sub_id), {"scorecard", sc})
    rendered = render(view)
    assert rendered =~ "caveat"
    assert rendered =~ "Domain mismatch"
  end
end
