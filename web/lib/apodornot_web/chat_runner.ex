defmodule ApodornotWeb.ChatRunner do
  @moduledoc """
  Spawns a supervised task that consumes the SSE stream from the Python chat
  service (`apodornot.web` at `POST /chat`) and forwards each event as a
  message to a target PID.

  The target LiveView's `handle_info/2` then turns those into UI updates
  (token deltas appended to the current assistant turn, tool_use indicators,
  and a final `done` flag flip).
  """

  require Logger

  @doc """
  Spawn a chat runner. ``target`` is the PID that should receive messages
  shaped as ``{:chat_event, ref, event_type, payload}``. ``ref`` lets the
  caller correlate this stream with a specific assistant turn (in case of
  rapid repeated submissions).
  """
  def start(target, ref, scorecard, messages, opts \\ []) do
    image_path = Keyword.get(opts, :image_path)
    equipment_context = Keyword.get(opts, :equipment_context)

    Task.Supervisor.start_child(ApodornotWeb.PipelineTaskSup, fn ->
      run(target, ref, scorecard, messages, image_path, equipment_context)
    end)
  end

  defp run(target, ref, scorecard, messages, image_path, equipment_context) do
    url = pipeline_url() <> "/chat"

    body =
      %{"scorecard" => scorecard, "messages" => messages}
      |> maybe_put("image_path", image_path)
      |> maybe_put("equipment_context", equipment_context)

    try do
      Req.post!(
        url,
        json: body,
        receive_timeout: :infinity,
        into: fn {:data, chunk}, acc ->
          for {event_type, payload} <- ApodornotWeb.PipelineRunner.parse_sse(chunk) do
            send(target, {:chat_event, ref, event_type, payload})
          end

          {:cont, acc}
        end
      )
    rescue
      e ->
        Logger.error("ChatRunner crashed: #{Exception.message(e)}")
        send(target, {:chat_event, ref, "error", %{"message" => Exception.message(e)}})
        send(target, {:chat_event, ref, "done", %{}})
    end
  end

  defp maybe_put(map, _, nil), do: map
  defp maybe_put(map, _, ""), do: map
  defp maybe_put(map, k, v), do: Map.put(map, k, v)

  defp pipeline_url, do: Application.fetch_env!(:apodornot_web, :pipeline_url)
end
