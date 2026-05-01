defmodule ApodornotWebWeb.AxisDiagnostics do
  @moduledoc """
  Per-axis diagnostic SVG components plus a quantile box-and-whisker chart.

  When the scorecard JSON includes a ``stage_diagnostics`` map (built by
  apodornot.diagnostics.build_diagnostics on the Python side), each axis
  visualization is driven by real per-stage data:

    - star_field — actual fitted star positions, eccentricities, position angles
    - noise      — per-channel mean/std (UI draws Gaussians from those)
    - target_psd — actual radial power spectrum array
    - gradient   — actual downsampled background heatmap
    - color_cmd  — actual per-star B-V proxy + flux-derived magnitudes

  When stage_diagnostics is missing or a sub-key is empty, the components
  show an empty frame with a "no data" note rather than synthetic mockups —
  honesty over decoration.
  """

  use Phoenix.Component

  @accent "#7dd3fc"

  # ------------------------------------------------------------------------ #
  # Top-level dispatch
  # ------------------------------------------------------------------------ #

  attr :axis, :string, required: true
  attr :data, :map, default: %{}

  def axis_diagnostic(assigns) do
    ~H"""
    <%= case @axis do %>
      <% "Star quality" -> %>       <.star_quality_diag data={@data["star_field"]} />
      <% "Noise management" -> %>   <.noise_diag data={@data["noise"]} />
      <% "Detail resolution" -> %>  <.freq_diag data={@data["target_psd"]} />
      <% "Gradient control" -> %>   <.gradient_diag data={@data["gradient"]} />
      <% "Color calibration" -> %>  <.color_diag data={@data["color_cmd"]} />
      <% _ -> %>                    <span></span>
    <% end %>
    """
  end

  # ------------------------------------------------------------------------ #
  # Frame
  # ------------------------------------------------------------------------ #

  attr :title, :string, required: true
  attr :subtitle, :string, default: nil
  slot :inner_block, required: true

  defp diag_frame(assigns) do
    ~H"""
    <div class="border border-slate-800 bg-slate-900/30 p-4 rounded">
      <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500">
        {@title}
      </div>
      <div :if={@subtitle} class="text-slate-400 text-xs mt-1 mb-3 leading-relaxed">
        {@subtitle}
      </div>
      <div class="mt-2">
        {render_slot(@inner_block)}
      </div>
    </div>
    """
  end

  defp empty_frame(assigns) do
    ~H"""
    <.diag_frame title={@title}>
      <div class="text-slate-600 italic text-sm py-4">no data</div>
    </.diag_frame>
    """
  end

  # ------------------------------------------------------------------------ #
  # 1. Star quality — eccentricity vector field (REAL DATA)
  # ------------------------------------------------------------------------ #

  attr :data, :map, default: nil

  def star_quality_diag(%{data: nil} = assigns), do: empty_frame(%{title: "Eccentricity vector field"})
  def star_quality_diag(%{data: %{"stars" => []}} = assigns), do: empty_frame(%{title: "Eccentricity vector field"})

  def star_quality_diag(assigns) do
    data = assigns.data
    image_w = data["image_w"] || 1
    image_h = data["image_h"] || 1
    # Fixed plot canvas; image coords get rescaled.
    w = 560
    h = 320
    sx = w / image_w
    sy = h / image_h

    cells =
      for s <- data["stars"] do
        x = s["x"] * sx
        y = s["y"] * sy
        ecc = s["ecc"] || 0.0
        pa = (s["pa_deg"] || 0.0) * :math.pi() / 180.0
        len = 6 + ecc * 16
        cos_a = :math.cos(pa)
        sin_a = :math.sin(pa)
        x1 = x - cos_a * len / 2
        y1 = y - sin_a * len / 2
        x2 = x + cos_a * len / 2
        y2 = y + sin_a * len / 2
        opacity = min(1.0, 0.4 + ecc * 0.8)

        %{
          cx: round_(x), cy: round_(y),
          x1: round_(x1), y1: round_(y1),
          x2: round_(x2), y2: round_(y2),
          opacity: round_(opacity, 2)
        }
      end

    median_ecc = data["median_ecc"]
    pattern = data["ecc_pattern"] || ""
    n_fitted = data["n_fitted"] || length(cells)

    label =
      "n=#{n_fitted} fitted · median e=#{format_num(median_ecc)} · pattern: #{pattern}"

    assigns = assign(assigns, w: w, h: h, cells: cells, accent: @accent, label: label)

    ~H"""
    <.diag_frame
      title="Eccentricity vector field"
      subtitle="Each tick = one fitted star. Length encodes eccentricity, orientation encodes major-axis. Uniform direction across the field = tracking error; radial pattern = optical aberration; random = atmospheric seeing."
    >
      <svg viewBox={"0 0 #{@w} #{@h}"} class="w-full block">
        <rect width={@w} height={@h} fill="rgba(255,255,255,0.015)" />
        <line x1={@w/2} y1="0" x2={@w/2} y2={@h} stroke="rgba(255,255,255,0.05)" />
        <line x1="0" y1={@h/2} x2={@w} y2={@h/2} stroke="rgba(255,255,255,0.05)" />
        <g :for={c <- @cells}>
          <line x1={c.x1} y1={c.y1} x2={c.x2} y2={c.y2}
                stroke={@accent} stroke-opacity={c.opacity} stroke-width="1.2" />
          <circle cx={c.cx} cy={c.cy} r="0.8" fill="rgba(255,255,255,0.6)" />
        </g>
        <text x="10" y={@h - 10} fill="rgba(255,255,255,0.5)"
              font-family="ui-monospace, monospace" font-size="10"
              style="letter-spacing: 0.06em">
          {@label}
        </text>
      </svg>
    </.diag_frame>
    """
  end

  # ------------------------------------------------------------------------ #
  # 2. Noise — per-channel histogram (REAL DATA)
  # ------------------------------------------------------------------------ #

  attr :data, :map, default: nil

  def noise_diag(%{data: nil} = _), do: empty_frame(%{title: "Background noise distribution"})
  def noise_diag(%{data: %{"channels" => []}} = _), do: empty_frame(%{title: "Background noise distribution"})

  def noise_diag(assigns) do
    w = 560
    h = 200
    n = 80

    # Use only color channels, drop luminance for the histogram (it overlaps).
    channels =
      assigns.data["channels"]
      |> Enum.filter(&(&1["name"] in ["R", "G", "B"]))

    # Determine plot x-domain from min/max across channels.
    means = Enum.map(channels, & &1["mean"])
    stds = Enum.map(channels, & &1["std"])
    min_x = (Enum.min(means, fn -> 0.0 end) - 4 * Enum.max(stds, fn -> 0.05 end)) |> max(0.0)
    max_x = (Enum.max(means, fn -> 1.0 end) + 4 * Enum.max(stds, fn -> 0.05 end)) |> min(1.0)
    span = max(max_x - min_x, 1.0e-9)

    # Find the peak Gaussian value across all channels (= 1/sigma) for normalization.
    peak = Enum.max(Enum.map(stds, fn s -> 1.0 / max(s, 1.0e-9) end), fn -> 1.0 end)

    paths =
      Enum.map(channels, fn ch ->
        mu = ch["mean"]
        sigma = max(ch["std"], 1.0e-9)
        points =
          for i <- 0..n do
            xv = min_x + i / n * span
            z = (xv - mu) / sigma
            y_val = :math.exp(-0.5 * z * z) / sigma / peak
            px = i / n * w
            py = (h - 24) - y_val * (h - 50) * 0.9
            "#{round_(px, 1)},#{round_(py, 1)}"
          end

        path = "M" <> hd(points) <> "L" <> Enum.join(tl(points), "L") <> "L#{w},#{h - 24}Z"
        Map.merge(ch, %{"path" => path, "sigma_label" => format_num(sigma)})
      end)

    assigns = assign(assigns, w: w, h: h, channels: paths)

    ~H"""
    <.diag_frame
      title="Background noise distribution · per channel"
      subtitle="Histogram of background-pixel intensities, shown as the fitted Gaussian per channel. Tight, well-aligned curves = controlled noise; wide spread or shifted means = calibration issues."
    >
      <svg viewBox={"0 0 #{@w} #{@h}"} class="w-full block">
        <rect width={@w} height={@h} fill="rgba(255,255,255,0.015)" />
        <line x1="0" y1={@h - 24} x2={@w} y2={@h - 24} stroke="rgba(255,255,255,0.12)" />
        <g :for={ch <- @channels}>
          <path d={ch["path"]} fill={ch["color"]} fill-opacity="0.12"
                stroke={ch["color"]} stroke-width="1.2" stroke-linejoin="round" />
        </g>
        <g :for={{ch, i} <- Enum.with_index(@channels)} transform={"translate(#{16 + i * 130}, 18)"}>
          <rect width="10" height="10" fill={ch["color"]} fill-opacity="0.4" stroke={ch["color"]} />
          <text x="16" y="9" fill="rgba(255,255,255,0.7)"
                font-family="ui-monospace, monospace" font-size="11">
            {ch["name"]}  σ={ch["sigma_label"]}
          </text>
        </g>
      </svg>
    </.diag_frame>
    """
  end

  # ------------------------------------------------------------------------ #
  # 3. Detail — radial power spectrum (REAL DATA)
  # ------------------------------------------------------------------------ #

  attr :data, :map, default: nil

  def freq_diag(%{data: nil} = _), do: empty_frame(%{title: "Radial power spectrum"})
  def freq_diag(%{data: %{"freq" => []}} = _), do: empty_frame(%{title: "Radial power spectrum"})

  def freq_diag(assigns) do
    w = 560
    h = 220
    freqs = assigns.data["freq"]
    powers = assigns.data["power"]

    f_max = Enum.max(freqs)
    f_min = max(Enum.min(freqs), 1.0e-6)

    # Log-power range
    log_powers = Enum.map(powers, fn p -> :math.log10(max(p, 1.0e-30)) end)
    p_min = Enum.min(log_powers)
    p_max = Enum.max(log_powers)
    p_span = max(p_max - p_min, 1.0e-9)

    pts =
      Enum.zip(freqs, log_powers)
      |> Enum.map(fn {f, lp} ->
        # log-x mapping for clarity
        log_f = :math.log10(max(f, f_min))
        x_norm = (log_f - :math.log10(f_min)) / max(:math.log10(f_max) - :math.log10(f_min), 1.0e-9)
        px = x_norm * w
        py = 20 + (1 - (lp - p_min) / p_span) * (h - 40)
        {round_(px, 1), round_(py, 1)}
      end)

    path =
      pts
      |> Enum.with_index()
      |> Enum.map(fn {{px, py}, i} -> "#{if i == 0, do: "M", else: "L"}#{px},#{py}" end)
      |> Enum.join("")

    eff_res = assigns.data["effective_resolution_cyc_per_px"]
    slope = assigns.data["spectral_slope"]

    eff_res_x =
      if eff_res do
        log_er = :math.log10(max(eff_res, f_min))
        (log_er - :math.log10(f_min)) / max(:math.log10(f_max) - :math.log10(f_min), 1.0e-9) * w
      else
        nil
      end

    assigns = assign(assigns,
      w: w, h: h, path: path, eff_res_x: eff_res_x,
      eff_res: eff_res, slope: slope, accent: @accent
    )

    ~H"""
    <.diag_frame
      title="Radial power spectrum"
      subtitle={"Log-log power vs spatial frequency. Real astronomical detail follows a power-law falloff (slope #{format_num(@slope)}); the knee where power flattens marks the effective resolution."}
    >
      <svg viewBox={"0 0 #{@w} #{@h}"} class="w-full block">
        <rect width={@w} height={@h} fill="rgba(255,255,255,0.015)" />
        <g :for={d <- 0..3}>
          <line x1="0" y1={20 + d / 4 * (@h - 40)} x2={@w} y2={20 + d / 4 * (@h - 40)}
                stroke="rgba(255,255,255,0.06)" />
        </g>
        <line :if={@eff_res_x} x1={@eff_res_x} y1="10" x2={@eff_res_x} y2={@h - 10}
              stroke={@accent} stroke-opacity="0.5" stroke-dasharray="3 3" />
        <text :if={@eff_res_x} x={@eff_res_x + 4} y="20" fill={@accent}
              font-family="ui-monospace, monospace" font-size="10">
          eff res = {format_num(@eff_res)} cy/px
        </text>
        <path d={@path} fill="none" stroke={@accent} stroke-width="1.4" />
        <text x={@w - 80} y={@h - 6} fill="rgba(255,255,255,0.4)"
              font-family="ui-monospace, monospace" font-size="9">
          frequency (log) →
        </text>
      </svg>
    </.diag_frame>
    """
  end

  # ------------------------------------------------------------------------ #
  # 4. Gradient — background heatmap (REAL DATA)
  # ------------------------------------------------------------------------ #

  attr :data, :map, default: nil

  def gradient_diag(%{data: nil} = _), do: empty_frame(%{title: "Background flatness map"})
  def gradient_diag(%{data: %{"values" => []}} = _), do: empty_frame(%{title: "Background flatness map"})

  def gradient_diag(assigns) do
    w = 560
    h = 220
    cols = assigns.data["cols"]
    rows = assigns.data["rows"]
    values = assigns.data["values"]
    cw = w / cols
    rh = h / rows

    cells =
      for {row, j} <- Enum.with_index(values),
          {v, i} <- Enum.with_index(row) do
        %{x: i * cw, y: j * rh, w: cw + 0.5, h: rh + 0.5, color: heat_color(v)}
      end

    raw_min = assigns.data["raw_min"]
    raw_max = assigns.data["raw_max"]
    range_label =
      if is_number(raw_min) and is_number(raw_max),
        do: "background range #{format_num(raw_min)} → #{format_num(raw_max)} (peak-to-peak #{format_num(raw_max - raw_min)})",
        else: ""

    assigns = assign(assigns, w: w, h: h, cells: cells, range_label: range_label)

    ~H"""
    <.diag_frame
      title="Background flatness map"
      subtitle="Downsampled SEP background model after foreground masking. Smooth color = flat sky. Strong gradient = uncorrected flat field, light pollution, or vignetting."
    >
      <svg viewBox={"0 0 #{@w} #{@h}"} class="w-full block">
        <rect width={@w} height={@h} fill="#000" />
        <rect :for={c <- @cells} x={c.x} y={c.y} width={c.w} height={c.h} fill={c.color} />
        <text x="10" y={@h - 10} fill="rgba(255,255,255,0.6)"
              font-family="ui-monospace, monospace" font-size="10">
          {@range_label}
        </text>
      </svg>
    </.diag_frame>
    """
  end

  defp heat_color(v) when v < 0.5 do
    k = v / 0.5
    "rgba(13, 32, 56, #{round_(0.4 + k * 0.3, 2)})"
  end

  defp heat_color(v) do
    k = (v - 0.5) / 0.5
    r = round(125 + k * 130)
    g = round(211 - k * 80)
    b = round(252 - k * 200)
    a = round_(0.5 + k * 0.4, 2)
    "rgba(#{r}, #{g}, #{b}, #{a})"
  end

  # ------------------------------------------------------------------------ #
  # 5. Color — stellar color-magnitude diagram (REAL DATA)
  # ------------------------------------------------------------------------ #

  attr :data, :map, default: nil

  def color_diag(%{data: nil} = _), do: empty_frame(%{title: "Stellar color–magnitude diagram"})
  def color_diag(%{data: %{"stars" => []}} = _), do: empty_frame(%{title: "Stellar color–magnitude diagram"})

  def color_diag(assigns) do
    w = 560
    h = 260
    stars_in = assigns.data["stars"] || []

    # bv_proxy in log10(R/B); typical real range is roughly [-0.4, 1.0].
    bv_min = -0.4
    bv_max = 1.5
    mag_min = 8.0
    mag_max = 14.0

    stars =
      Enum.map(stars_in, fn s ->
        bv = max(bv_min, min(bv_max, s["bv_proxy"] || 0.0))
        mag = max(mag_min, min(mag_max, s["mag"] || 11.0))
        x = 40 + (bv - bv_min) / (bv_max - bv_min) * (w - 60)
        y = 20 + (mag - mag_min) / (mag_max - mag_min) * (h - 50)
        size = max(1.5, 5.0 - (mag - mag_min) * 0.45)
        # Use the actual measured RGB to color the dot.
        rgb = s["rgb"] || [0.5, 0.5, 0.5]
        [r, g, b] = rgb
        scale = max(r, max(g, b))
        scale = if scale > 0, do: 255.0 / scale, else: 0.0
        color = "rgba(#{round(r * scale)}, #{round(g * scale)}, #{round(b * scale)}, 0.85)"
        %{x: round_(x, 1), y: round_(y, 1), color: color, r: round_(size, 1)}
      end)

    bv_ticks = [-0.4, 0.0, 0.5, 1.0, 1.5]
    mag_ticks = [8, 10, 12, 14]

    assigns = assign(assigns, w: w, h: h, stars: stars,
      bv_ticks: bv_ticks, mag_ticks: mag_ticks,
      bv_min: bv_min, bv_max: bv_max, mag_min: mag_min, mag_max: mag_max)

    ~H"""
    <.diag_frame
      title="Stellar color–magnitude diagram"
      subtitle="Each dot = one detected star, colored by its measured RGB. Position is log10(R/B) (color index proxy) vs instrumental magnitude. Healthy spread = good color preservation; cluster collapse = color destroyed in processing."
    >
      <svg viewBox={"0 0 #{@w} #{@h}"} class="w-full block">
        <rect width={@w} height={@h} fill="rgba(255,255,255,0.015)" />
        <line x1="40" y1={@h - 30} x2={@w - 10} y2={@h - 30} stroke="rgba(255,255,255,0.18)" />
        <line x1="40" y1="20" x2="40" y2={@h - 30} stroke="rgba(255,255,255,0.18)" />
        <text :for={v <- @bv_ticks} x={40 + (v - @bv_min) / (@bv_max - @bv_min) * (@w - 60)} y={@h - 14}
              text-anchor="middle" fill="rgba(255,255,255,0.45)"
              font-family="ui-monospace, monospace" font-size="10">
          {format_num(v)}
        </text>
        <text :for={m <- @mag_ticks} x="32" y={20 + (m - @mag_min) / (@mag_max - @mag_min) * (@h - 50) + 3}
              text-anchor="end" fill="rgba(255,255,255,0.45)"
              font-family="ui-monospace, monospace" font-size="10">{m}</text>
        <text x={@w - 10} y={@h - 14} text-anchor="end" fill="rgba(255,255,255,0.5)"
              font-family="ui-monospace, monospace" font-size="10">log10(R/B) →</text>
        <text x="48" y="26" fill="rgba(255,255,255,0.5)"
              font-family="ui-monospace, monospace" font-size="10">mag</text>
        <circle :for={s <- @stars} cx={s.x} cy={s.y} r={s.r} fill={s.color} />
      </svg>
    </.diag_frame>
    """
  end

  # ------------------------------------------------------------------------ #
  # Quantile chart (per-component, in the drawer below the diagnostic)
  # ------------------------------------------------------------------------ #

  attr :metric, :string, required: true
  attr :value, :any, required: true
  attr :percentile, :float, required: true
  attr :higher_is_better, :boolean, required: true
  attr :quantiles, :map, required: true

  def quantile_chart(assigns) do
    q = assigns.quantiles
    p10 = q["p10"] || 0.0
    p25 = q["p25"] || 0.0
    p50 = q["p50"] || 0.0
    p75 = q["p75"] || 0.0
    p90 = q["p90"] || 0.0
    lo = p10 - (p25 - p10) * 0.5
    hi = p90 + (p90 - p75) * 0.5
    span = max(hi - lo, 1.0e-9)
    x = fn v -> max(0.0, min(100.0, (v - lo) / span * 100)) end

    value =
      case assigns.value do
        v when is_number(v) -> v
        _ -> p50
      end

    assigns = assign(assigns,
      x_p10: round_(x.(p10), 1),
      x_p25: round_(x.(p25), 1),
      x_p50: round_(x.(p50), 1),
      x_p75: round_(x.(p75), 1),
      x_p90: round_(x.(p90), 1),
      x_value: round_(x.(value), 1),
      lbl_p10: format_num(p10),
      lbl_p50: format_num(p50),
      lbl_p90: format_num(p90),
      lbl_value: format_num(value),
      accent: @accent
    )

    ~H"""
    <div class="border border-slate-800 rounded p-4">
      <div class="flex justify-between items-baseline mb-2">
        <div class="text-slate-100 text-sm font-medium">{@metric}</div>
        <div class="font-mono text-[10px] text-slate-500">
          {if @higher_is_better, do: "higher better", else: "lower better"}
        </div>
      </div>
      <div class="flex justify-between font-mono text-[11px] text-slate-500 tabular-nums mb-1">
        <span>p10 {@lbl_p10}</span>
        <span>p50 {@lbl_p50}</span>
        <span>p90 {@lbl_p90}</span>
      </div>
      <svg viewBox="0 0 100 22" preserveAspectRatio="none" class="w-full block h-8">
        <rect x={@x_p10} y="9" width={@x_p90 - @x_p10} height="4" fill="rgba(255,255,255,0.08)" />
        <rect x={@x_p25} y="7" width={@x_p75 - @x_p25} height="8" fill="rgba(255,255,255,0.12)" />
        <line x1={@x_p50} y1="5" x2={@x_p50} y2="17" stroke="rgba(255,255,255,0.35)" stroke-width="1" />
        <line x1={@x_value} y1="2" x2={@x_value} y2="20" stroke={@accent} stroke-width="1.5" />
        <circle cx={@x_value} cy="11" r="2.5" fill={@accent} />
      </svg>
      <div class="flex justify-between font-mono text-[11px] tabular-nums mt-1">
        <span style={"color: #{@accent}"}>your value: {@lbl_value}</span>
        <span class="text-slate-500">{format_num(@percentile)}th pct</span>
      </div>
    </div>
    """
  end

  # ------------------------------------------------------------------------ #
  # Helpers
  # ------------------------------------------------------------------------ #

  defp round_(x), do: round_(x, 1)
  defp round_(x, decimals) when is_number(x) do
    factor = :math.pow(10, decimals)
    trunc(x * factor) / factor
  end

  defp format_num(nil), do: "—"
  defp format_num(n) when is_integer(n), do: Integer.to_string(n)
  defp format_num(n) when is_float(n) do
    cond do
      abs(n) >= 100 -> :erlang.float_to_binary(n, decimals: 0)
      abs(n) >= 10 -> :erlang.float_to_binary(n, decimals: 1)
      true -> :erlang.float_to_binary(n, decimals: 2)
    end
  end
  defp format_num(_), do: "—"
end
