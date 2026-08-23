defmodule ApodornotWeb.PipelineRunnerTest do
  use ExUnit.Case, async: true

  alias ApodornotWeb.PipelineRunner

  describe "parse_sse/1" do
    test "parses a single event" do
      chunk = "event: stage\ndata: {\"stage\":\"A1\",\"status\":\"done\"}\n\n"
      assert PipelineRunner.parse_sse(chunk) ==
               [{"stage", %{"stage" => "A1", "status" => "done"}}]
    end

    test "parses multiple events in one chunk" do
      chunk = """
      event: submission
      data: {"submission_id":"abc"}

      event: stage
      data: {"stage":"A1","status":"running","detail":"x"}

      """

      events = PipelineRunner.parse_sse(chunk)
      assert length(events) == 2
      assert {"submission", %{"submission_id" => "abc"}} in events
    end

    test "returns [] for empty chunk" do
      assert PipelineRunner.parse_sse("") == []
      assert PipelineRunner.parse_sse("\n\n") == []
    end

    test "skips malformed blocks" do
      chunk = "garbage without colons\n\nevent: stage\ndata: {\"x\":1}\n\n"
      events = PipelineRunner.parse_sse(chunk)
      # First block has no event/data, gets skipped; second is valid.
      assert events == [{"stage", %{"x" => 1}}]
    end

    test "handles event with empty data" do
      chunk = "event: done\ndata: {}\n\n"
      assert PipelineRunner.parse_sse(chunk) == [{"done", %{}}]
    end
  end

  describe "topic/1" do
    test "namespaces submission IDs" do
      assert PipelineRunner.topic("abc-123") == "submission:abc-123"
    end
  end
end
