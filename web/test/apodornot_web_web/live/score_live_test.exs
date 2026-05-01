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

  test "chat sidebar is open by default once a scorecard is loaded", %{conn: conn} do
    sub_id = "test-sub-chat-1"
    {:ok, view, _html} = live(conn, ~p"/s/#{sub_id}")

    refute render(view) =~ "grounded in your scorecard"

    sc = %{
      "image_path" => "x.jpg", "target_category" => "rosette",
      "reference_category" => "rosette", "reference_n" => 33,
      "input_domain" => "display", "reference_domain" => "display",
      "warnings" => [], "overall_score" => 72.0,
      "axes" => [], "metrics" => [], "diagnostics" => []
    }
    PubSub.broadcast(@pubsub, PipelineRunner.topic(sub_id), {"scorecard", sc})

    rendered = render(view)
    # Open by default — sidebar visible, with the chat input + placeholder.
    assert rendered =~ "grounded in your scorecard"
    assert rendered =~ "ask about a metric"

    # Collapse, then re-open.
    rendered = view |> element("button[phx-click='toggle_chat']") |> render_click()
    assert rendered =~ "ask claude"  # collapsed-tab label
    refute rendered =~ "ask about a metric"

    rendered = view |> element("button[phx-click='toggle_chat']") |> render_click()
    assert rendered =~ "ask about a metric"
  end

  test "chat tokens stream into the active turn and finalize on done", %{conn: conn} do
    sub_id = "test-sub-chat-2"
    {:ok, view, _html} = live(conn, ~p"/s/#{sub_id}")

    sc = %{
      "image_path" => "x.jpg", "target_category" => "rosette",
      "reference_category" => "rosette", "reference_n" => 33,
      "input_domain" => "display", "reference_domain" => "display",
      "warnings" => [], "overall_score" => 72.0,
      "axes" => [], "metrics" => [], "diagnostics" => []
    }
    PubSub.broadcast(@pubsub, PipelineRunner.topic(sub_id), {"scorecard", sc})

    # Manually drive the chat by sending events to the LiveView process —
    # bypasses the actual Anthropic call.
    pid = view.pid
    ref = :sys.get_state(pid).socket.assigns.chat_active_ref || make_ref()
    send(pid, {:chat_event, ref, "token", %{"text" => "Hello "}})
    send(pid, {:chat_event, ref, "token", %{"text" => "there."}})
    send(pid, {:chat_event, ref, "done", %{}})
    _ = :sys.get_state(pid)
    rendered = render(view)
    assert rendered =~ "grounded in your scorecard"
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
