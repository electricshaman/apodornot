# Phoenix LiveView ↔ apodornot integration sketch

The Python side (`apodornot.web`) lives in this repo. The Phoenix side lives in a separate repo (call it `apodornot_web`). This document sketches both ends of the contract concretely so the porting agent has a complete reference.

## The wire contract

The Python service exposes two endpoints:

- `GET /healthz` → `{"status": "ok"}`
- `GET /evaluate?image_path=<abs>&target_type=<optional>` → `text/event-stream`
- `GET /reference?target_type=<name>&archive_dir=<path>` → JSON listing of APOD reference entries

The streaming `/evaluate` endpoint emits these SSE events in order:

```
event: submission
data: {"submission_id": "uuid-..."}

event: stage
data: {"stage": "A1", "status": "running", "detail": "characterizing image"}

event: stage
data: {"stage": "A1", "status": "done", "detail": "1046 sources (486 stars, 483 extended)"}

... (A2 through A6 each emit running + done) ...

event: scorecard
data: { ...full scorecard JSON, shape per docs/ui-design-prompt.md... }

event: done
data: {}
```

If the pipeline raises, you get an `event: error` with `{"type": "...", "message": "..."}` instead of `scorecard`, then `event: done` always closes the stream.

## Python side (already in this repo)

Run alongside Phoenix on `127.0.0.1`:

```bash
apodornot-web --host 127.0.0.1 --port 8000 --workers 4
```

`--workers N` runs N independent uvicorn worker processes — one per CPU you want to use for parallel evaluations. The pipeline holds the GIL while it's in numpy/scipy C code, so multi-worker is the right concurrency primitive (not threads).

**Don't bind to a public interface.** The endpoints accept arbitrary local file paths and have no auth.

## Phoenix side

### Application supervisor

```elixir
# lib/apodornot_web/application.ex
def start(_type, _args) do
  children = [
    ApodornotWebWeb.Endpoint,
    {Phoenix.PubSub, name: ApodornotWeb.PubSub},
    {Task.Supervisor, name: ApodornotWeb.PipelineTaskSup},
    {Registry, keys: :unique, name: ApodornotWeb.SubmissionRegistry}
  ]

  Supervisor.start_link(children, strategy: :one_for_one, name: ApodornotWeb.Supervisor)
end
```

### Config

```elixir
# config/runtime.exs
config :apodornot_web,
  pipeline_url: System.get_env("APODORNOT_PIPELINE_URL") || "http://127.0.0.1:8000"
```

### PipelineRunner — one Task per submission

```elixir
defmodule ApodornotWeb.PipelineRunner do
  @moduledoc """
  Spawns a task that consumes an SSE stream from the Python pipeline service
  and re-broadcasts each event onto the submission's PubSub topic.
  """
  alias Phoenix.PubSub

  @pubsub ApodornotWeb.PubSub

  def start(submission_id, image_path, target_type \\ nil) do
    Task.Supervisor.start_child(ApodornotWeb.PipelineTaskSup, fn ->
      run(submission_id, image_path, target_type)
    end)
  end

  defp run(submission_id, image_path, target_type) do
    url = pipeline_url() <> "/evaluate"
    params = [image_path: image_path, target_type: target_type]

    Req.get!(url,
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
      PubSub.broadcast(
        @pubsub,
        topic(submission_id),
        {"error", %{"type" => "ReqError", "message" => Exception.message(e)}}
      )
      PubSub.broadcast(@pubsub, topic(submission_id), {"done", %{}})
  end

  def topic(submission_id), do: "submission:" <> submission_id

  defp pipeline_url, do: Application.fetch_env!(:apodornot_web, :pipeline_url)

  # Minimal SSE parser. For production, use a proper streaming parser
  # (e.g. server_sent_events or hand-rolled with a buffer that survives
  # chunk boundaries — this version assumes whole events per chunk).
  defp parse_sse(chunk) do
    chunk
    |> String.split("\n\n", trim: true)
    |> Enum.map(fn block ->
      lines = String.split(block, "\n")
      event = lines |> Enum.find_value("message", &extract(&1, "event: "))
      data = lines |> Enum.find_value("{}", &extract(&1, "data: "))
      {event, Jason.decode!(data)}
    end)
  end

  defp extract(line, prefix) do
    if String.starts_with?(line, prefix), do: String.replace_prefix(line, prefix, "")
  end
end
```

### ScoreLive — the page that owns the loading + scorecard view

```elixir
defmodule ApodornotWebWeb.ScoreLive do
  use ApodornotWebWeb, :live_view
  alias Phoenix.PubSub
  alias ApodornotWeb.PipelineRunner

  @pubsub ApodornotWeb.PubSub

  def mount(%{"submission_id" => id}, %{"image_path" => path, "target_type" => target}, socket) do
    if connected?(socket), do: PubSub.subscribe(@pubsub, PipelineRunner.topic(id))

    socket =
      socket
      |> assign(submission_id: id, image_path: path, target_type: target,
                scorecard: nil, error: nil, selected_stage: nil)
      |> stream(:stages, [])

    if connected?(socket), do: PipelineRunner.start(id, path, target)

    {:ok, socket}
  end

  # SSE → PubSub → handle_info
  def handle_info({"stage", payload}, socket) do
    # Use stage name as the stream key so the same stage's running/done
    # entries replace each other in the UI.
    row = Map.put(payload, "id", payload["stage"])
    {:noreply, stream_insert(socket, :stages, row)}
  end

  def handle_info({"scorecard", payload}, socket) do
    {:noreply, assign(socket, :scorecard, payload)}
  end

  def handle_info({"error", payload}, socket) do
    {:noreply, assign(socket, :error, payload)}
  end

  def handle_info({"done", _}, socket), do: {:noreply, socket}
  def handle_info({"submission", _}, socket), do: {:noreply, socket}

  def handle_event("select_stage", %{"stage" => stage}, socket) do
    {:noreply, assign(socket, :selected_stage, stage)}
  end

  def render(assigns) do
    ~H"""
    <%= cond do %>
      <% @error -> %>
        <.failure_panel error={@error} />
      <% @scorecard -> %>
        <.scorecard_view scorecard={@scorecard} on_axis_click={JS.show(to: "#stage-drawer")} />
      <% true -> %>
        <.stage_log stages={@streams.stages} />
    <% end %>

    <.portal id="drawer-portal" target="body">
      <dialog id="stage-drawer" phx-mounted={JS.ignore_attributes(["open"])}>
        <.stage_detail :if={@selected_stage} stage={@selected_stage} scorecard={@scorecard} />
      </dialog>
    </.portal>
    """
  end
end
```

### Stage log function component

```elixir
def stage_log(assigns) do
  ~H"""
  <div id="stage-log" phx-update="stream" class="font-mono text-sm space-y-1">
    <div :for={{dom_id, stage} <- @stages} id={dom_id}
         class={["flex gap-4", stage["status"] == "done" && "text-emerald-400"]}>
      <span class="w-12 text-slate-500">{stage["stage"]}</span>
      <span class="w-20">{stage["status"]}</span>
      <span class="text-slate-300">{stage["detail"]}</span>
    </div>
  </div>
  """
end
```

That's the full loop. SSE → PubSub → `stream_insert` → DOM patch. No JS hooks, no polling.

### Failure modes worth handling

1. **Python service down.** `Req.get!` raises; the rescue in `PipelineRunner.run/3` broadcasts an `error` event so the LiveView shows a real message instead of hanging on a spinner.
2. **SSE chunk boundaries.** The `parse_sse/1` above assumes one event per chunk, which is true for small payloads on localhost but not guaranteed in general. For production, buffer chunks and split on `\n\n` only when you've seen the terminator — there are SSE parser libs on hex if you want one off the shelf.
3. **LiveView reconnect mid-pipeline.** Because the pipeline state lives in PubSub broadcasts that have already happened, a reconnect won't replay them. If you want survivability, have the runner write a small ETS row per submission with the latest scorecard, and `mount/3` reads from ETS as well as subscribing.

## Local dev

A Procfile-style boot for both services:

```bash
# In the apodornot Python repo
.venv/bin/apodornot-web --port 8000 &
PY_PID=$!

# In the apodornot_web Phoenix repo
APODORNOT_PIPELINE_URL=http://127.0.0.1:8000 mix phx.server &
EX_PID=$!

trap "kill $PY_PID $EX_PID" EXIT
wait
```

Or use [overmind](https://github.com/DarthSim/overmind) / [foreman](https://github.com/ddollar/foreman) with a `Procfile`:

```
pipeline: cd ../apodornot && .venv/bin/apodornot-web --port 8000
web:      APODORNOT_PIPELINE_URL=http://127.0.0.1:8000 mix phx.server
```

## Why this shape

- Python is the source of truth for measurement; Phoenix is the source of truth for the user session and rendering. Each side does what it's good at.
- SSE keeps the Phoenix code simple — no WebSocket from Python, no long polling, no custom framing. `text/event-stream` is curl-debuggable and survives the load balancer if you ever need one.
- The `scorecard` event is the same JSON shape the React UI consumes, so the LiveView and any future React/native client share one contract.
- The MCP server (`apodornot-mcp`) is unchanged — it imports the same `evaluate_image` and `score_evaluation` functions. Two thin facades, one core.
