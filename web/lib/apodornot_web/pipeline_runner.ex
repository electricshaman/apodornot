defmodule ApodornotWeb.PipelineRunner do
  @moduledoc """
  Spawns a supervised task that consumes the SSE stream from the Python
  pipeline service (`apodornot.web` at `/evaluate`) and re-broadcasts each
  event onto a per-submission `Phoenix.PubSub` topic.

  LiveViews subscribe to `topic(submission_id)` in `mount/3` and receive
  `{event_type, payload}` tuples for `submission`, `stage`, `scorecard`,
  `error`, `done` events.
  """

  require Logger
  alias Phoenix.PubSub

  @pubsub ApodornotWeb.PubSub

  @doc """
  Spawn a runner task. Returns the spawned task PID; the actual results
  arrive via PubSub on `topic(submission_id)`.
  """
  def start(submission_id, image_path, target_type \\ nil) do
    Task.Supervisor.start_child(ApodornotWeb.PipelineTaskSup, fn ->
      run(submission_id, image_path, target_type)
    end)
  end

  @doc "PubSub topic for a submission's events."
  def topic(submission_id), do: "submission:" <> submission_id

  defp run(submission_id, image_path, target_type) do
    url = pipeline_url() <> "/evaluate"

    params =
      [{"image_path", image_path}] ++
        if(target_type in [nil, ""], do: [], else: [{"target_type", target_type}])

    Logger.info("PipelineRunner: starting #{submission_id} for #{image_path}")

    try do
      Req.get!(
        url,
        params: params,
        receive_timeout: :infinity,
        into: fn {:data, chunk}, acc ->
          for {event_type, payload} <- parse_sse(chunk) do
            PubSub.broadcast(@pubsub, topic(submission_id), {event_type, payload})
          end

          {:cont, acc}
        end
      )
    rescue
      e ->
        Logger.error("PipelineRunner crashed: #{Exception.message(e)}")

        PubSub.broadcast(
          @pubsub,
          topic(submission_id),
          {"error", %{"type" => "ReqError", "message" => Exception.message(e)}}
        )

        PubSub.broadcast(@pubsub, topic(submission_id), {"done", %{}})
    end
  end

  @doc false
  # Naive SSE parser: assumes whole events per chunk. Good enough for localhost
  # streams; for arbitrary chunk boundaries, buffer and split on \n\n only when
  # the terminator is seen.
  def parse_sse(chunk) do
    chunk
    |> String.split("\n\n", trim: true)
    |> Enum.map(&parse_block/1)
    |> Enum.reject(&is_nil/1)
  end

  defp parse_block(block) do
    lines = String.split(block, "\n")
    event = Enum.find_value(lines, &extract(&1, "event: "))
    data = Enum.find_value(lines, &extract(&1, "data: "))

    cond do
      is_nil(event) -> nil
      is_nil(data) -> {event, %{}}
      true ->
        case Jason.decode(data) do
          {:ok, payload} -> {event, payload}
          {:error, _} -> nil
        end
    end
  end

  defp extract(line, prefix) do
    if String.starts_with?(line, prefix), do: String.replace_prefix(line, prefix, "")
  end

  defp pipeline_url, do: Application.fetch_env!(:apodornot_web, :pipeline_url)
end
