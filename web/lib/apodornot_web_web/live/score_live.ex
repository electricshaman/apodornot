defmodule ApodornotWebWeb.ScoreLive do
  use ApodornotWebWeb, :live_view

  alias ApodornotWeb.{ChatRunner, PipelineRunner}
  alias ApodornotWebWeb.AxisDiagnostics
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
       selected_axis: nil,
       # Chat state
       chat_open: false,
       chat_messages: [],          # [%{role, content}]
       chat_streaming: false,
       chat_active_ref: nil,       # ref for the in-flight stream
       chat_active_text: "",       # accumulating assistant text for the current turn
       chat_tool_uses: [],         # tool_use events surfaced for the current turn
       chat_draft: ""
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

  def handle_info({:chat_event, ref, type, payload}, socket)
      when ref != socket.assigns.chat_active_ref do
    # Stale stream from a previous turn — ignore.
    _ = {type, payload}
    {:noreply, socket}
  end

  def handle_info({:chat_event, _ref, "token", %{"text" => text}}, socket) do
    {:noreply, assign(socket, :chat_active_text, socket.assigns.chat_active_text <> text)}
  end

  def handle_info({:chat_event, _ref, "tool_use", payload}, socket) do
    {:noreply,
     assign(socket, :chat_tool_uses, socket.assigns.chat_tool_uses ++ [payload])}
  end

  def handle_info({:chat_event, _ref, "done", _}, socket) do
    text = socket.assigns.chat_active_text
    new_messages =
      if text == "" do
        socket.assigns.chat_messages
      else
        socket.assigns.chat_messages ++ [%{role: "assistant", content: text}]
      end

    {:noreply,
     assign(socket,
       chat_messages: new_messages,
       chat_streaming: false,
       chat_active_ref: nil,
       chat_active_text: "",
       chat_tool_uses: []
     )}
  end

  def handle_info({:chat_event, _ref, "error", %{"message" => msg}}, socket) do
    new_messages =
      socket.assigns.chat_messages ++ [%{role: "assistant", content: "(error: #{msg})"}]

    {:noreply,
     assign(socket,
       chat_messages: new_messages,
       chat_streaming: false,
       chat_active_ref: nil,
       chat_active_text: "",
       chat_tool_uses: []
     )}
  end

  def handle_info({:chat_event, _ref, "close", _}, socket), do: {:noreply, socket}

  def handle_info(_, socket), do: {:noreply, socket}

  def handle_event("select_axis", %{"axis" => axis}, socket) do
    {:noreply, assign(socket, :selected_axis, axis)}
  end

  def handle_event("close_drawer", _, socket) do
    {:noreply, assign(socket, :selected_axis, nil)}
  end

  def handle_event("toggle_chat", _, socket) do
    {:noreply, assign(socket, :chat_open, !socket.assigns.chat_open)}
  end

  def handle_event("update_draft", %{"chat" => %{"draft" => draft}}, socket) do
    {:noreply, assign(socket, :chat_draft, draft)}
  end

  def handle_event("send_chat", _params, %{assigns: %{scorecard: nil}} = socket) do
    {:noreply, socket}
  end

  def handle_event("send_chat", _params, %{assigns: %{chat_streaming: true}} = socket) do
    {:noreply, socket}
  end

  def handle_event("send_chat", _params, socket) do
    draft = String.trim(socket.assigns.chat_draft || "")

    if draft == "" do
      {:noreply, socket}
    else
      messages = socket.assigns.chat_messages ++ [%{role: "user", content: draft}]
      ref = make_ref()

      ChatRunner.start(self(), ref, socket.assigns.scorecard, messages,
        image_path: socket.assigns.scorecard["image_path"])

      {:noreply,
       socket
       |> assign(
         chat_messages: messages,
         chat_draft: "",
         chat_streaming: true,
         chat_active_ref: ref,
         chat_active_text: "",
         chat_tool_uses: []
       )}
    end
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

      <.chat_widget
        :if={@scorecard}
        open={@chat_open}
        messages={@chat_messages}
        streaming={@chat_streaming}
        active_text={@chat_active_text}
        tool_uses={@chat_tool_uses}
        draft={@chat_draft}
      />
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
    p = max(0.0, min(100.0, score * 1.0))
    assigns = assign(assigns, score: score, color: color, p: p)

    ~H"""
    <button
      phx-click="select_axis"
      phx-value-axis={@axis["axis"]}
      class="text-left border border-slate-800 bg-slate-900/30 hover:border-slate-700 hover:bg-slate-900/50 rounded p-4 flex flex-col gap-3 transition-colors"
    >
      <div class="flex justify-between items-baseline">
        <div class="text-slate-300 text-xs font-medium tracking-wide">
          {@axis["axis"]}
        </div>
        <div class="font-mono text-xl text-slate-100 tabular-nums">
          {format_score(@score)}
        </div>
      </div>

      <.percentile_bar p={@p} color={@color} />

      <div class="flex flex-col gap-2 mt-1">
        <div :for={c <- (@axis["components"] || []) |> Enum.take(3)} class="flex flex-col gap-0.5 font-mono text-[11px] tabular-nums">
          <div class="flex justify-between gap-2 items-baseline">
            <span class="font-sans text-[11px] text-slate-500 truncate">
              {(c["label"] && c["label"] != "" && c["label"]) || format_metric_label(c["metric"])}
            </span>
            <span class="text-slate-500 shrink-0">p{format_score(c["percentile"])}</span>
          </div>
          <span class="text-slate-200 truncate">{format_value_with_unit(c["value"], c["unit"], c["format"])}</span>
        </div>
      </div>
    </button>
    """
  end

  attr :p, :float, required: true
  attr :color, :string, required: true

  defp percentile_bar(assigns) do
    ~H"""
    <svg viewBox="0 0 100 14" preserveAspectRatio="none" class="w-full block h-3.5">
      <rect x="0" y="4" width="100" height="6" fill="rgba(255,255,255,0.06)" rx="3" />
      <rect x="25" y="4" width="50" height="6" fill="rgba(255,255,255,0.05)" />
      <line x1="50" y1="2" x2="50" y2="12" stroke="rgba(255,255,255,0.3)" stroke-width="1" />
      <rect x="0" y="4" width={@p} height="6" fill={@color} fill-opacity="0.25" />
      <line x1={@p} y1="1" x2={@p} y2="13" stroke={@color} stroke-width="1.5" />
      <circle cx={@p} cy="7" r="2.5" fill={@color} />
    </svg>
    """
  end

  defp format_metric_label(nil), do: "—"
  defp format_metric_label(name) when is_binary(name) do
    name
    |> String.replace("_", " ")
    |> String.replace(~r/\s+px$/, " (px)")
  end
  defp format_metric_label(other), do: to_string(other)

  defp format_value_with_unit(nil, _, _), do: "—"
  defp format_value_with_unit(v, unit, fmt) when is_number(v) do
    rendered =
      case fmt do
        "scientific" -> sci_notation(v)
        "decimal" -> decimal_str(v)
        "px" -> :erlang.float_to_binary(v * 1.0, decimals: 2)
        _ -> default_num(v)
      end

    if unit && unit != "", do: "#{rendered} #{unit}", else: rendered
  end
  defp format_value_with_unit(other, _, _), do: to_string(other)

  defp sci_notation(0.0), do: "0"
  defp sci_notation(v) when is_number(v) do
    abs_v = abs(v)
    cond do
      abs_v >= 0.001 and abs_v < 1000 -> decimal_str(v)
      true ->
        exp = floor(:math.log10(abs_v))
        mantissa = v / :math.pow(10, exp)
        "#{:erlang.float_to_binary(mantissa * 1.0, decimals: 2)}e#{if exp >= 0, do: "+", else: ""}#{trunc(exp)}"
    end
  end

  defp decimal_str(v) when is_number(v) do
    abs_v = abs(v * 1.0)
    cond do
      abs_v >= 1000 -> :erlang.float_to_binary(v * 1.0, decimals: 0)
      abs_v >= 10 -> :erlang.float_to_binary(v * 1.0, decimals: 1)
      abs_v >= 1 -> :erlang.float_to_binary(v * 1.0, decimals: 2)
      true -> :erlang.float_to_binary(v * 1.0, decimals: 3)
    end
  end

  defp default_num(v) when is_integer(v), do: Integer.to_string(v)
  defp default_num(v) when is_number(v), do: decimal_str(v)

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
      <div class="p-6 space-y-6">
        <%
          axis_data = Enum.find(@scorecard["axes"], &(&1["axis"] == @axis))
          components = (axis_data && axis_data["components"]) || []
          all_metrics = @scorecard["metrics"] || []
        %>

        <AxisDiagnostics.axis_diagnostic axis={@axis} data={@scorecard["stage_diagnostics"] || %{}} />

        <div>
          <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500 mb-3">
            component metrics
          </div>
          <div class="space-y-4">
            <%= for c <- components, m = Enum.find(all_metrics, &(&1["metric"] == c["metric"])), m do %>
              <AxisDiagnostics.quantile_chart
                metric={c["metric"]}
                value={c["value"]}
                percentile={c["percentile"]}
                higher_is_better={c["higher_is_better"]}
                quantiles={m["quantiles"] || %{}}
              />
            <% end %>
          </div>
        </div>
      </div>
    </aside>
    """
  end

  defp chat_widget(assigns) do
    ~H"""
    <%= if @open do %>
      <div class="fixed bottom-0 right-0 z-50 w-[min(440px,100vw)] h-[min(620px,80vh)] bg-slate-900 border-l border-t border-slate-800 rounded-tl flex flex-col shadow-2xl">
        <div class="flex justify-between items-center px-4 py-3 border-b border-slate-800">
          <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500">
            chat · grounded in your scorecard
          </div>
          <button phx-click="toggle_chat" class="text-slate-500 hover:text-slate-200 font-mono text-sm">
            close
          </button>
        </div>

        <div class="flex-1 overflow-y-auto p-4 space-y-3 text-sm">
          <div :if={@messages == [] and not @streaming} class="text-slate-500 italic">
            Ask anything about your scorecard. Claude has access to the metric knowledge base.
          </div>

          <div :for={m <- @messages} class={[
            "leading-relaxed whitespace-pre-wrap",
            m.role == "user" && "text-slate-300 font-mono text-xs uppercase tracking-wider before:content-['you_·_']",
            m.role == "assistant" && "text-slate-100"
          ]}>
            {m.content}
          </div>

          <div :if={@streaming} class="text-slate-100 leading-relaxed whitespace-pre-wrap">
            <span :if={@active_text != ""}>{@active_text}</span>
            <span :for={t <- @tool_uses} class="block font-mono text-[10px] uppercase tracking-widest text-sky-400/70 mt-2">
              ↳ checking {t["name"]}
            </span>
            <span :if={@active_text == "" and @tool_uses == []} class="text-slate-500 italic">claude is thinking…</span>
          </div>
        </div>

        <form phx-submit="send_chat" phx-change="update_draft" class="border-t border-slate-800 p-3 flex gap-2">
          <input
            type="text"
            name="chat[draft]"
            value={@draft}
            placeholder={if @streaming, do: "wait…", else: "ask about a metric, axis, or finding…"}
            disabled={@streaming}
            autocomplete="off"
            class="flex-1 bg-slate-950 border border-slate-800 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-sky-400 disabled:opacity-50"
          />
          <button type="submit" disabled={@streaming or String.trim(@draft) == ""}
                  class="px-3 py-2 bg-sky-400 hover:bg-sky-300 text-slate-950 text-sm font-medium rounded disabled:opacity-30 disabled:cursor-not-allowed">
            send
          </button>
        </form>
      </div>
    <% else %>
      <button phx-click="toggle_chat"
              class="fixed bottom-6 right-6 z-50 px-4 py-3 bg-sky-400 hover:bg-sky-300 text-slate-950 rounded-full font-medium shadow-lg">
        ask claude
      </button>
    <% end %>
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
