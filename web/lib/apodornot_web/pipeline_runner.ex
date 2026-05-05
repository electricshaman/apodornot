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
        # On Fly, ``<app>.internal`` only resolves over IPv6 flycast.
        # Without ``:inet6`` Finch defaults to IPv4 → :nxdomain →
        # PipelineRunner crashes with "non-existing domain" before the
        # request ever leaves the machine.
        connect_options: [transport_opts: [:inet6]],
        # The accumulator is a per-stream buffer of bytes that haven't yet
        # ended in `\n\n`. Large events (the scorecard payload, which
        # carries the per-stage diagnostics blob) routinely span multiple
        # chunks, so we buffer until we see a terminator.
        into: fn {:data, chunk}, {req, resp} ->
          priv = resp.private || %{}
          buf = Map.get(priv, :sse_buf, "") <> chunk
          {events, rest} = drain_events(buf)

          Logger.debug(
            "PipelineRunner #{submission_id}: chunk=#{byte_size(chunk)}b " <>
              "events=#{Enum.map(events, fn {t, _} -> t end) |> Enum.join(",")} " <>
              "buffered=#{byte_size(rest)}b"
          )

          for {event_type, payload} <- events do
            PubSub.broadcast(@pubsub, topic(submission_id), {event_type, payload})
          end

          {:cont, {req, %{resp | private: Map.put(priv, :sse_buf, rest)}}}
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
  # Stateless one-shot parser: assumes whole events per chunk. Retained for
  # the chat stream (per-token events are small) and for tests. New callers
  # should use ``drain_events/1`` instead.
  def parse_sse(chunk) do
    chunk
    |> String.split("\n\n", trim: true)
    |> Enum.map(&parse_block/1)
    |> Enum.reject(&is_nil/1)
  end

  @doc false
  # Pull complete `\n\n`-terminated events out of ``buf``, returning the
  # parsed events plus whatever trailing partial event is still waiting for
  # its terminator. Large events (scorecard, ~50 KB+ of stage diagnostics)
  # routinely arrive across multiple TCP chunks; without this buffering the
  # tail event was getting silently dropped.
  def drain_events(buf) do
    case String.split(buf, "\n\n") do
      [tail] ->
        {[], tail}

      blocks_and_tail ->
        {blocks, [tail]} = Enum.split(blocks_and_tail, -1)

        events =
          blocks
          |> Enum.map(&parse_block/1)
          |> Enum.reject(&is_nil/1)

        {events, tail}
    end
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
          {:ok, payload} ->
            {event, payload}

          {:error, err} ->
            # Don't drop silently — events disappearing into the void cost
            # us a debugging session when Python sent NaN literals. Log
            # the failure so the next mismatch is obvious.
            Logger.warning(
              "PipelineRunner: dropped #{event} event, JSON decode failed: " <>
                Exception.message(err) <> " (data preview: #{String.slice(data, 0, 200)})"
            )
            nil
        end
    end
  end

  defp extract(line, prefix) do
    if String.starts_with?(line, prefix), do: String.replace_prefix(line, prefix, "")
  end

  defp pipeline_url, do: Application.fetch_env!(:apodornot_web, :pipeline_url)
end
