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

  alias ApodornotWebWeb.Glossary

  @accent "#7dd3fc"

  # Map raw metric keys to glossary term ids so the per-component label in the
  # quantile chart links to its definition.
  @metric_to_term %{
    "median_fwhm_px" => "fwhm",
    "median_eccentricity" => "eccentricity",
    "fwhm_corner_excess" => "corner_fwhm_excess",
    "fwhm_quadrant_asymmetry" => "fwhm_quadrant_asymmetry",
    "noise_floor_l" => "background_sigma",
    "psd_spectral_slope" => "noise_psd_slope",
    "psd_high_band_suppression" => "hf_suppression",
    "autocorr_width_px" => "autocorrelation",
    "snr_target_median" => "snr",
    "fpn_max_pattern" => "fixed_pattern_noise",
    "target_spectral_slope" => "detail_slope",
    "target_effective_resolution" => "mtf50",
    "gradient_ratio" => "gradient_ratio",
    "vignetting_falloff" => "vignetting",
    "color_balance_magnitude" => "channel_balance",
    "color_overall_score" => "axis.color_calibration",
    "star_diversity_score" => "stellar_chroma",
    "background_chroma_distance" => "background_chroma",
    "chroma_concentration" => "chroma_concentration"
  }

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
  attr :title_term, :string, default: nil
  attr :subtitle, :string, default: nil
  slot :inner_block, required: true
  slot :subtitle_block

  defp diag_frame(assigns) do
    ~H"""
    <div class="border border-slate-800 bg-slate-900/30 p-4 rounded">
      <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500">
        <%= if @title_term do %>
          <Glossary.term id={@title_term}>{@title}</Glossary.term>
        <% else %>
          {@title}
        <% end %>
      </div>
      <div :if={@subtitle_block != []} class="text-slate-400 text-xs mt-1 mb-3 leading-relaxed">
        {render_slot(@subtitle_block)}
      </div>
      <div :if={@subtitle && @subtitle_block == []} class="text-slate-400 text-xs mt-1 mb-3 leading-relaxed">
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
    <.diag_frame title="Eccentricity vector field" title_term="vector_field">
      <:subtitle_block>
        Each tick = one fitted star. Length encodes <Glossary.term id="eccentricity">eccentricity</Glossary.term>,
        orientation encodes the <Glossary.term id="position_angle">major-axis position angle</Glossary.term>.
        Uniform direction across the field = <Glossary.term id="tracking_error">tracking error</Glossary.term>;
        radial pattern = <Glossary.term id="optical_aberration">optical aberration</Glossary.term>;
        random = <Glossary.term id="random_seeing">atmospheric seeing</Glossary.term>.
      </:subtitle_block>
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
    <.diag_frame title="Background noise distribution · per channel">
      <:subtitle_block>
        Per-channel <Glossary.term id="background_sigma">background σ</Glossary.term> shown as the
        fitted Gaussian. Tight, well-aligned curves = controlled noise;
        wide spread or shifted means = calibration issues.
      </:subtitle_block>
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

    # Decade ticks for the log-x axis. Caps at a few sensible values
    # within the actual frequency range.
    log_f_min = :math.log10(f_min)
    log_f_max = :math.log10(f_max)

    x_ticks =
      [0.01, 0.02, 0.05, 0.1, 0.2, 0.5]
      |> Enum.filter(fn v -> v >= f_min and v <= f_max end)
      |> Enum.map(fn v ->
        log_v = :math.log10(v)
        x = (log_v - log_f_min) / max(log_f_max - log_f_min, 1.0e-9) * w
        {v, x}
      end)

    assigns = assign(assigns,
      w: w, h: h, path: path, eff_res_x: eff_res_x,
      eff_res: eff_res, slope: slope, x_ticks: x_ticks, accent: @accent
    )

    ~H"""
    <.diag_frame title="Radial power spectrum" title_term="psd">
      <:subtitle_block>
        Log-log power vs spatial frequency from the <Glossary.term id="azimuthal_average">azimuthally averaged</Glossary.term> 2D FFT.
        Real detail follows a power-law falloff (slope {format_num(@slope)});
        the knee where power flattens marks the
        <Glossary.term id="mtf50">effective resolution</Glossary.term>.
      </:subtitle_block>
      <svg viewBox={"0 0 #{@w} #{@h}"} class="w-full block">
        <rect width={@w} height={@h} fill="rgba(255,255,255,0.015)" />
        <g :for={d <- 0..3}>
          <line x1="0" y1={20 + d / 4 * (@h - 40)} x2={@w} y2={20 + d / 4 * (@h - 40)}
                stroke="rgba(255,255,255,0.06)" />
        </g>

        <%!-- decade x-axis ticks + labels --%>
        <g :for={{val, tx} <- @x_ticks}>
          <line x1={tx} y1={@h - 24} x2={tx} y2={@h - 18}
                stroke="rgba(255,255,255,0.35)" />
          <text x={tx} y={@h - 6} fill="rgba(255,255,255,0.55)"
                text-anchor="middle" font-family="ui-monospace, monospace" font-size="9">
            {val}
          </text>
        </g>

        <line :if={@eff_res_x} x1={@eff_res_x} y1="10" x2={@eff_res_x} y2={@h - 24}
              stroke={@accent} stroke-opacity="0.5" stroke-dasharray="3 3" />
        <%!-- anchor label on whichever side has more room --%>
        <text :if={@eff_res_x}
              x={if @eff_res_x > @w / 2, do: @eff_res_x - 4, else: @eff_res_x + 4}
              y="20" fill={@accent}
              text-anchor={if @eff_res_x > @w / 2, do: "end", else: "start"}
              font-family="ui-monospace, monospace" font-size="10">
          eff res = {format_num(@eff_res)} cy/px
        </text>
        <path d={@path} fill="none" stroke={@accent} stroke-width="1.4" />
        <text x={@w - 6} y="12" fill="rgba(255,255,255,0.4)"
              text-anchor="end"
              font-family="ui-monospace, monospace" font-size="9">
          frequency (cy/px, log)
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
    <.diag_frame title="Background flatness map">
      <:subtitle_block>
        Downsampled SEP background model after foreground masking. Smooth
        color = flat sky. Strong <Glossary.term id="gradient_ratio">gradient</Glossary.term> = uncorrected
        flat field or light pollution; corner darkening = <Glossary.term id="vignetting">vignetting</Glossary.term>.
      </:subtitle_block>
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
    <.diag_frame title="Stellar color–magnitude diagram" title_term="color_magnitude_diagram">
      <:subtitle_block>
        Each dot = one detected star, colored by its measured RGB. Position is
        log10(R/B) (a <Glossary.term id="bv_index">B–V color index</Glossary.term> proxy) vs instrumental magnitude.
        Healthy spread = good <Glossary.term id="stellar_chroma">stellar chroma</Glossary.term>;
        cluster collapse = color destroyed in processing.
      </:subtitle_block>
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
  attr :label, :string, default: nil
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

    # Always render the bar as "worst → best, left → right" so the user's
    # marker position agrees with the rank_score interpretation.
    # For lower-is-better metrics this inverts the raw-value axis (so the
    # NUMERICALLY-LARGEST raw value, which is the worst, sits on the left).
    x =
      if assigns.higher_is_better do
        fn v -> max(0.0, min(100.0, (v - lo) / span * 100)) end
      else
        fn v -> max(0.0, min(100.0, (hi - v) / span * 100)) end
      end

    value =
      case assigns.value do
        v when is_number(v) -> v
        _ -> p50
      end

    # The bar is always oriented worst (left) → best (right). For
    # higher-is-better metrics, the LEFT label is "p10" (worst tail) and the
    # RIGHT label is "p90" (best tail). For lower-is-better metrics the
    # numeric order flips so the LEFT label becomes "p90" (worst, biggest
    # value) and the RIGHT label is "p10" (best, smallest value).
    {left_label, left_value, right_label, right_value} =
      if assigns.higher_is_better do
        {"p10", p10, "p90", p90}
      else
        {"p90", p90, "p10", p10}
      end

    # The "p25–p75" inner box: in raw-value mapping the box is between
    # x.(p25) and x.(p75). For lower-better, x.(p25) > x.(p75) so we
    # take min/max for safety.
    box_lo = min(x.(p25), x.(p75))
    box_hi = max(x.(p25), x.(p75))
    whisker_lo = min(x.(p10), x.(p90))
    whisker_hi = max(x.(p10), x.(p90))

    assigns = assign(assigns,
      x_p50: round_(x.(p50), 1),
      x_value: round_(x.(value), 1),
      x_box_lo: round_(box_lo, 1),
      x_box_w: round_(box_hi - box_lo, 1),
      x_whisker_lo: round_(whisker_lo, 1),
      x_whisker_w: round_(whisker_hi - whisker_lo, 1),
      left_label: left_label,
      left_value: format_num(left_value),
      right_label: right_label,
      right_value: format_num(right_value),
      lbl_p50: format_num(p50),
      lbl_value: format_num(value),
      accent: @accent,
      term_id: Map.get(@metric_to_term, assigns.metric),
      display_label: assigns.label || assigns.metric
    )

    ~H"""
    <div class="border border-slate-800 rounded p-4">
      <div class="flex justify-between items-baseline mb-2">
        <div class="text-slate-100 text-sm font-medium">
          <%= if @term_id do %>
            <Glossary.term id={@term_id}>{@display_label}</Glossary.term>
          <% else %>
            {@display_label}
          <% end %>
        </div>
        <div class="font-mono text-[10px] text-slate-500">
          {if @higher_is_better, do: "higher better", else: "lower better"}
        </div>
      </div>
      <div class="flex justify-between font-mono text-[11px] text-slate-500 tabular-nums mb-1">
        <span><Glossary.term id={@left_label}>{@left_label}</Glossary.term> {@left_value}</span>
        <span><Glossary.term id="p50">p50</Glossary.term> {@lbl_p50}</span>
        <span><Glossary.term id={@right_label}>{@right_label}</Glossary.term> {@right_value}</span>
      </div>
      <svg viewBox="0 0 100 22" preserveAspectRatio="none" class="w-full block h-8">
        <rect x={@x_whisker_lo} y="9" width={@x_whisker_w} height="4" fill="rgba(255,255,255,0.08)" />
        <rect x={@x_box_lo} y="7" width={@x_box_w} height="8" fill="rgba(255,255,255,0.12)" />
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
