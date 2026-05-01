defmodule ApodornotWebWeb.ScoreLive do
  use ApodornotWebWeb, :live_view

  alias ApodornotWeb.PipelineRunner
  alias Phoenix.PubSub

  @pubsub ApodornotWeb.PubSub

  def mount(%{"submission_id" => id} = params, _session, socket) do
    image_filename = Map.get(params, "image", "image")

    if connected?(socket) do
      PubSub.subscribe(@pubsub, PipelineRunner.topic(id))
    end

    {:ok,
     socket
     |> assign(
       submission_id: id,
       image_filename: image_filename,
       scorecard: nil,
       error: nil,
       selected_axis: nil
     )
     |> stream_configure(:stages, dom_id: &"stage-#{&1["stage"]}")
     |> stream(:stages, [])}
  end

  # Tag the row's id from the stage name so running → done overwrites in place.
  def handle_info({"stage", payload}, socket) do
    {:noreply, stream_insert(socket, :stages, payload)}
  end

  def handle_info({"scorecard", payload}, socket) do
    {:noreply, assign(socket, :scorecard, payload)}
  end

  def handle_info({"error", payload}, socket) do
    {:noreply, assign(socket, :error, payload)}
  end

  def handle_info({"done", _}, socket), do: {:noreply, socket}
  def handle_info({"submission", _}, socket), do: {:noreply, socket}
  def handle_info(_, socket), do: {:noreply, socket}

  def handle_event("select_axis", %{"axis" => axis}, socket) do
    {:noreply, assign(socket, :selected_axis, axis)}
  end

  def handle_event("close_drawer", _, socket) do
    {:noreply, assign(socket, :selected_axis, nil)}
  end

  def render(assigns) do
    ~H"""
    <div class="min-h-screen bg-slate-950 text-slate-100 font-sans">
      <.session_bar image_filename={@image_filename} scorecard={@scorecard} />

      <%= cond do %>
        <% @error -> %>
          <.failure_panel error={@error} />
        <% @scorecard -> %>
          <.scorecard_view
            scorecard={@scorecard}
            image_filename={@image_filename}
            selected_axis={@selected_axis}
          />
        <% true -> %>
          <.loading_view stages={@streams.stages} />
      <% end %>
    </div>
    """
  end

  # ----- function components ------------------------------------------------ #

  defp session_bar(assigns) do
    ~H"""
    <div class="fixed top-0 left-0 right-0 px-6 py-3 flex justify-between items-center z-10 pointer-events-none">
      <a href={~p"/"} class="font-mono text-xs uppercase tracking-widest text-slate-500 hover:text-slate-300 pointer-events-auto">
        apodornot
      </a>
      <div :if={@scorecard} class="font-mono text-[10px] tracking-wider text-slate-600">
        vs {String.upcase(@scorecard["reference_category"])} · n={@scorecard["reference_n"]}
      </div>
    </div>
    """
  end

  defp loading_view(assigns) do
    ~H"""
    <div class="max-w-3xl mx-auto pt-32 px-8 pb-16">
      <div class="font-mono text-xs uppercase tracking-widest text-slate-500 mb-2">pipeline</div>
      <div class="text-slate-300 mb-8">Measuring stages A1 → A6 against the APOD reference set.</div>

      <div id="stage-log" phx-update="stream" class="font-mono text-sm space-y-1">
        <div :for={{dom_id, stage} <- @stages} id={dom_id} class="flex gap-4 items-baseline">
          <span class="w-12 text-slate-500">{stage["stage"]}</span>
          <span class={[
            "w-20",
            stage["status"] == "done" && "text-emerald-400",
            stage["status"] == "running" && "text-sky-400 animate-pulse"
          ]}>
            {stage["status"]}
          </span>
          <span class="text-slate-300 truncate">{stage["detail"]}</span>
        </div>
      </div>
    </div>
    """
  end

  defp scorecard_view(assigns) do
    ~H"""
    <div class="max-w-6xl mx-auto px-8 pt-20 pb-16 space-y-6">
      <.warning_banner :if={@scorecard["warnings"] != []} warnings={@scorecard["warnings"]} />

      <div class="grid grid-cols-[1fr_minmax(280px,360px)] gap-6">
        <.image_panel image_filename={@image_filename} scorecard={@scorecard} />
        <.overall_score_panel scorecard={@scorecard} />
      </div>

      <.radar_chart :if={@scorecard["axes"] != []} axes={@scorecard["axes"]} />

      <div :if={@scorecard["axes"] != []} class="grid grid-cols-1 md:grid-cols-5 gap-3">
        <.axis_card :for={ax <- @scorecard["axes"]} axis={ax} />
      </div>

      <.findings_list diagnostics={@scorecard["diagnostics"]} />

      <.stage_drawer
        :if={@selected_axis}
        axis={@selected_axis}
        scorecard={@scorecard}
      />
    </div>
    """
  end

  defp warning_banner(assigns) do
    ~H"""
    <div class="border border-amber-700/50 bg-amber-900/10 px-4 py-3 rounded">
      <div class="font-mono text-[10px] uppercase tracking-widest text-amber-300 mb-1">⚠ caveat</div>
      <p :for={w <- @warnings} class="text-amber-100/90 text-sm">{w}</p>
    </div>
    """
  end

  defp image_panel(assigns) do
    ~H"""
    <div class="border border-slate-800 bg-slate-900/30 rounded p-4 flex flex-col gap-3">
      <div class="flex justify-between items-center font-mono text-xs">
        <span class="text-slate-200 truncate">{@image_filename}</span>
        <span class="text-slate-500 uppercase">{@scorecard["input_domain"]}</span>
      </div>
      <div class="flex-1 min-h-[280px] bg-black rounded flex items-center justify-center text-slate-700 font-mono text-sm">
        ({@scorecard["target_category"]})
      </div>
    </div>
    """
  end

  defp overall_score_panel(assigns) do
    score = assigns.scorecard["overall_score"] || 0
    color = tier_color(score)
    assigns = assign(assigns, score: score, color: color)

    ~H"""
    <div class="border border-slate-800 bg-slate-900/30 rounded p-6 flex flex-col justify-between gap-4">
      <div>
        <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500 mb-2">
          overall · 0–100
        </div>
        <div class="text-7xl font-light tabular-nums" style={"color: #{@color}"}>
          {trunc(@score)}
        </div>
      </div>
      <div class="font-mono text-xs text-slate-500 leading-relaxed">
        vs APOD {@scorecard["reference_category"]} · n={@scorecard["reference_n"]}<br />
        {@scorecard["reference_domain"]} domain
      </div>
    </div>
    """
  end

  defp axis_card(assigns) do
    score = assigns.axis["score"] || 0
    color = tier_color(score)
    assigns = assign(assigns, score: score, color: color)

    ~H"""
    <button
      phx-click="select_axis"
      phx-value-axis={@axis["axis"]}
      class="text-left border border-slate-800 bg-slate-900/30 rounded p-4 hover:border-slate-700 transition-colors"
    >
      <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500 mb-2">
        {@axis["axis"]}
      </div>
      <div class="text-2xl font-light tabular-nums mb-2" style={"color: #{@color}"}>
        {format_score(@score)}
      </div>
      <div class="h-1 bg-slate-800 rounded-full overflow-hidden">
        <div class="h-full" style={"width: #{@score}%; background: #{@color}"}></div>
      </div>
      <div class="mt-3 space-y-1 font-mono text-[11px]">
        <div :for={c <- (@axis["components"] || []) |> Enum.take(3)} class="flex justify-between text-slate-500">
          <span class="truncate">{c["metric"]}</span>
          <span class="tabular-nums text-slate-300">{format_score(c["percentile"])}</span>
        </div>
      </div>
    </button>
    """
  end

  defp findings_list(assigns) do
    ~H"""
    <div class="border border-slate-800 bg-slate-900/30 rounded p-5">
      <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500 mb-3">
        findings
      </div>
      <div :if={Enum.empty?(@diagnostics)} class="text-slate-500 italic text-sm">
        No notable weaknesses.
      </div>
      <ul class="space-y-2">
        <li :for={d <- @diagnostics} class="text-slate-200 text-sm leading-relaxed before:content-['—_'] before:text-slate-600">
          {d}
        </li>
      </ul>
    </div>
    """
  end

  defp stage_drawer(assigns) do
    ~H"""
    <div class="fixed inset-0 z-30 bg-black/55" phx-click="close_drawer"></div>
    <aside class="fixed top-0 right-0 bottom-0 w-[min(620px,100vw)] bg-slate-900 border-l border-slate-800 z-40 overflow-y-auto">
      <div class="p-6 border-b border-slate-800 flex justify-between items-center">
        <div>
          <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500 mb-1">axis</div>
          <div class="text-lg font-medium">{@axis}</div>
        </div>
        <button phx-click="close_drawer" class="text-slate-500 hover:text-slate-200 font-mono text-sm">
          close
        </button>
      </div>
      <div class="p-6 space-y-4">
        <%
          axis_data = Enum.find(@scorecard["axes"], &(&1["axis"] == @axis))
          components = (axis_data && axis_data["components"]) || []
        %>
        <div :for={c <- components} class="border border-slate-800 rounded p-4">
          <div class="font-mono text-xs text-slate-300 mb-2">{c["metric"]}</div>
          <div class="flex items-baseline gap-4 mb-2">
            <span class="text-2xl tabular-nums">{format_value(c["value"])}</span>
            <span class="font-mono text-xs text-slate-500">value</span>
            <span class="text-2xl tabular-nums ml-auto">{format_score(c["percentile"])}</span>
            <span class="font-mono text-xs text-slate-500">pct</span>
          </div>
          <div class="h-1 bg-slate-800 rounded-full overflow-hidden">
            <div class="h-full bg-sky-400" style={"width: #{c["percentile"] || 0}%"}></div>
          </div>
          <div class="mt-2 font-mono text-[10px] text-slate-600 uppercase tracking-wider">
            {if c["higher_is_better"], do: "higher is better", else: "lower is better"}
          </div>
        </div>
      </div>
    </aside>
    """
  end

  defp failure_panel(assigns) do
    ~H"""
    <div class="max-w-2xl mx-auto pt-32 px-8">
      <div class="font-mono text-xs uppercase tracking-widest text-rose-400 mb-2">pipeline error</div>
      <div class="text-rose-300">{@error["message"]}</div>
      <div class="mt-4 font-mono text-xs text-slate-600">{@error["type"]}</div>
      <a href={~p"/"} class="mt-8 inline-block text-sky-400 hover:underline">← Try another image</a>
    </div>
    """
  end

  # Custom polar SVG radar — no JS, no chart lib.
  defp radar_chart(assigns) do
    n = length(assigns.axes)
    cx = 200
    cy = 200
    r_max = 150

    # Polar grid rings at 20/40/60/80/100
    grid_points = Enum.map([20, 40, 60, 80, 100], fn pct ->
      r = r_max * pct / 100

      points =
        for i <- 0..(n - 1) do
          angle = -:math.pi() / 2 + 2 * :math.pi() * i / n
          x = cx + r * :math.cos(angle)
          y = cy + r * :math.sin(angle)
          "#{Float.round(x, 2)},#{Float.round(y, 2)}"
        end
        |> Enum.join(" ")

      %{points: points, pct: pct}
    end)

    spokes = for i <- 0..(n - 1) do
      angle = -:math.pi() / 2 + 2 * :math.pi() * i / n
      x2 = cx + r_max * :math.cos(angle)
      y2 = cy + r_max * :math.sin(angle)
      %{x2: Float.round(x2, 2), y2: Float.round(y2, 2)}
    end

    {polygon_points, labels} =
      assigns.axes
      |> Enum.with_index()
      |> Enum.map(fn {ax, i} ->
        score = ax["score"] || 0
        r = r_max * (score / 100)
        angle = -:math.pi() / 2 + 2 * :math.pi() * i / n
        x = cx + r * :math.cos(angle)
        y = cy + r * :math.sin(angle)
        label_r = r_max + 24
        lx = cx + label_r * :math.cos(angle)
        ly = cy + label_r * :math.sin(angle)
        {"#{Float.round(x, 2)},#{Float.round(y, 2)}",
         %{label: ax["axis"], lx: Float.round(lx, 2), ly: Float.round(ly, 2)}}
      end)
      |> Enum.unzip()

    polygon_points = Enum.join(polygon_points, " ")
    assigns = assign(assigns,
      cx: cx, cy: cy, r_max: r_max,
      grid_points: grid_points, spokes: spokes,
      polygon_points: polygon_points, labels: labels
    )

    ~H"""
    <div class="border border-slate-800 bg-slate-900/30 rounded p-6 flex justify-center">
      <svg viewBox="0 0 400 400" class="w-full max-w-md" style="aspect-ratio: 1/1">
        <polygon :for={g <- @grid_points} points={g.points}
                 fill="none" stroke="rgb(30 41 59)" stroke-width="1" />
        <line :for={s <- @spokes} x1={@cx} y1={@cy} x2={s.x2} y2={s.y2}
              stroke="rgb(30 41 59)" stroke-width="1" />
        <polygon points={@polygon_points}
                 fill="rgb(56 189 248 / 0.18)"
                 stroke="rgb(56 189 248)"
                 stroke-width="2" />
        <text :for={l <- @labels} x={l.lx} y={l.ly}
              text-anchor="middle" dominant-baseline="middle"
              fill="rgb(148 163 184)"
              style="font-family: monospace; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase">
          {l.label}
        </text>
      </svg>
    </div>
    """
  end

  defp tier_color(s) when is_number(s) do
    cond do
      s >= 80 -> "#bef264"
      s >= 60 -> "#7dd3fc"
      s >= 40 -> "#fbbf24"
      true    -> "#fb7185"
    end
  end
  defp tier_color(_), do: "#7dd3fc"

  defp format_score(nil), do: "—"
  defp format_score(s) when is_number(s), do: :erlang.float_to_binary(s * 1.0, decimals: 0)
  defp format_score(_), do: "—"

  defp format_value(nil), do: "—"
  defp format_value(v) when is_number(v) do
    if abs(v) >= 100 or v != trunc(v) do
      :erlang.float_to_binary(v * 1.0, decimals: 2)
    else
      "#{trunc(v)}"
    end
  end
  defp format_value(_), do: "—"
end
