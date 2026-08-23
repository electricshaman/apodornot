defmodule ApodornotWebWeb.Glossary do
  @moduledoc """
  Hand-written glossary of astrophotography / signal-processing terms used in
  the scorecard UI. Each entry has three explanation levels — Beginner /
  Intermediate / Advanced — that the user can toggle through when they click a
  jargon term in the stage-detail drawer.

  ## Where the content lives

  All glossary entries are in **`priv/glossary/glossary.json`**. Edit that file
  to add a term or refine wording — no Elixir change required, no LLM round-trip.
  The file is loaded at compile time via `@external_resource` so dev recompiles
  on save.

  ## Adding a new term

      // priv/glossary/glossary.json
      "my_new_term": {
        "label": "My new term",
        "beginner": "...",
        "intermediate": "...",
        "advanced": "..."
      }

  Then reference it in HEEx with `<.term id="my_new_term">jargon text</.term>`.

  ## Future i18n

  This is the right shape for adding language localization later. The skill
  level stays in the data structure (one file per language, e.g. `glossary.es.json`),
  not in Gettext locales — so we don't end up with `es_beginner`, `fr_advanced`
  combinations cluttering Gettext's locale axis.
  """

  use Phoenix.Component

  @glossary_path Path.join([:code.priv_dir(:apodornot_web), "glossary", "glossary.json"])
  @external_resource @glossary_path

  @raw @glossary_path |> File.read!() |> Jason.decode!()

  # Validate at compile time that every entry has the required fields, so
  # typos in the JSON fail fast rather than rendering nil at runtime.
  @required_keys ~w(label beginner intermediate advanced)
  for {term_id, entry} <- @raw do
    missing = @required_keys -- Map.keys(entry)
    if missing != [] do
      raise "glossary entry #{inspect(term_id)} missing keys: #{inspect(missing)}"
    end
  end

  @entries @raw

  # --------------------------------------------------------------------------
  # Auto-built alias map: phrase => term_id, derived from each entry's label.
  # --------------------------------------------------------------------------
  # Labels like "FWHM (full width at half maximum)" produce both "FWHM" and
  # "full width at half maximum". Labels like "p10 / 10th percentile" produce
  # both halves. Optional ``aliases: [...]`` array on an entry adds custom
  # phrases for words the LLM uses naturally (e.g. "tracking" → tracking_error).
  #
  # Inlined into the module body because @attribute expansion can't call
  # ``defp`` helpers in the same compilation pass.
  @aliases (
    @entries
    |> Enum.flat_map(fn {term_id, entry} ->
      label = entry["label"] || ""

      paren_aliases =
        ~r/\(([^)]+)\)/
        |> Regex.scan(label, capture: :all_but_first)
        |> List.flatten()

      cleaned = Regex.replace(~r/\s*\([^)]*\)/, label, "") |> String.trim()

      split_aliases = String.split(cleaned, ~r/\s+\/\s+|\s+[—–]\s+/, trim: true)
      explicit = entry["aliases"] || []

      (split_aliases ++ paren_aliases ++ [cleaned] ++ explicit)
      |> Enum.map(&String.trim/1)
      |> Enum.reject(&(&1 == ""))
      |> Enum.uniq()
      |> Enum.map(&{&1, term_id})
    end)
    |> Enum.sort_by(fn {phrase, _} -> -String.length(phrase) end)
  )

  @doc false
  # Compile-time list of `{phrase, term_id}` tuples used by linkify_html/1.
  # Exposed for tests; not part of the public component surface.
  def aliases, do: @aliases

  @doc "Look up a glossary entry by id, returning a map %{label, beginner, intermediate, advanced} or nil."
  def lookup(term_id), do: Map.get(@entries, term_id)

  @doc "Return all glossary keys (useful for tests and unused-term checks)."
  def keys, do: Map.keys(@entries)

  # --------------------------------------------------------------------------
  # linkify_html — auto-link glossary terms in chat HTML output.
  # --------------------------------------------------------------------------

  # Tags whose contents we never want to walk: <a> would create nested
  # clickables, <pre> would mangle preformatted code blocks, and any
  # element we already wrapped should be left alone. We DO walk into
  # <code> because the chat assistant emits raw metric ids in inline
  # backticks (`fwhm_corner_excess`) and the user expects those to be
  # clickable — see the glossary aliases for the snake_case metric ids.
  @protected_tags ~w(a pre button)

  @doc """
  Walk an HTML string and wrap any glossary term in a clickable button.

  Used by `ScoreLive` to post-process Earmark output so the assistant's
  chat replies become explorable — every "FWHM", "tracking error",
  "PSD slope", etc. becomes a click target into the same explanation
  panel that powers the stage drawer.

  ## Options

    - `:exclude` — a term_id to skip when linkifying (e.g., when rendering
      that term's own explanation panel — we don't want "FWHM" in the
      definition of FWHM to link back to itself).
  """
  def linkify_html(html, opts \\ [])

  def linkify_html(html, opts) when is_binary(html) and html != "" do
    exclude = Keyword.get(opts, :exclude)

    case Floki.parse_fragment(html) do
      {:ok, tree} ->
        tree
        |> Enum.flat_map(&walk(&1, exclude))
        |> Floki.raw_html(encode: false, pretty: false)

      {:error, _} ->
        html
    end
  end
  def linkify_html(other, _opts), do: other

  # Walk the parsed tree. ``text`` nodes (binary children) are scanned for
  # glossary phrases; anything inside @protected_tags is passed through.
  # Always returns a list so the caller can flat_map across the tree.
  defp walk({tag, attrs, children}, _exclude) when tag in @protected_tags do
    [{tag, attrs, children}]
  end
  defp walk({tag, attrs, children}, exclude) do
    [{tag, attrs, Enum.flat_map(children, &walk_child(&1, exclude))}]
  end
  defp walk(text, exclude) when is_binary(text) do
    linkify_text(text, exclude)
  end
  defp walk(other, _exclude), do: [other]

  defp walk_child({tag, attrs, children} = node, exclude) do
    if tag in @protected_tags do
      [node]
    else
      [{tag, attrs, Enum.flat_map(children, &walk_child(&1, exclude))}]
    end
  end
  defp walk_child(text, exclude) when is_binary(text) do
    linkify_text(text, exclude)
  end
  defp walk_child(other, _exclude), do: [other]

  # Match the longest alias starting at each position. Greedy via the
  # length-sorted alias list. Returns a list of mixed text-binaries and
  # button-tuples that Floki can serialize.
  defp linkify_text(text, exclude) do
    # ``prev`` tracks the codepoint immediately before ``rest`` so we can
    # require a word boundary on the LEFT side of any match. Starting state
    # 0 means "beginning of string" which counts as a boundary.
    do_linkify(text, 0, exclude, [])
    |> Enum.reverse()
  end

  defp do_linkify("", _prev, _exclude, acc), do: acc
  defp do_linkify(rest, prev, exclude, acc) do
    if not alphanumeric?(prev) do
      case match_term(rest, exclude) do
        {phrase, term_id, after_match} when after_match == "" ->
          [build_button(phrase, term_id) | acc]

        {phrase, term_id, <<next::utf8, _::binary>> = after_match} ->
          if not alphanumeric?(next) do
            do_linkify(after_match, next, exclude, [build_button(phrase, term_id) | acc])
          else
            advance_one(rest, exclude, acc)
          end

        :nomatch ->
          advance_one(rest, exclude, acc)
      end
    else
      advance_one(rest, exclude, acc)
    end
  end

  defp advance_one(rest, exclude, acc) do
    <<ch::utf8, more::binary>> = rest
    do_linkify(more, ch, exclude, [<<ch::utf8>> | acc])
  end

  defp build_button(phrase, term_id) do
    {"button",
     [
       {"type", "button"},
       {"phx-click", "select_term"},
       {"phx-value-term", term_id},
       {"class",
        "text-sky-300/90 underline decoration-dotted decoration-sky-400/40 underline-offset-2 hover:decoration-sky-300 cursor-help bg-transparent border-0 p-0 font-inherit"}
     ], [phrase]}
  end

  # Try each alias in length-desc order, skipping any that point at
  # ``exclude`` so we don't self-link inside a term's own explanation.
  # First hit wins.
  defp match_term(text, exclude) do
    Enum.reduce_while(@aliases, :nomatch, fn {phrase, term_id}, _ ->
      cond do
        term_id == exclude ->
          {:cont, :nomatch}

        true ->
          lower_text = String.downcase(text)
          lower_phrase = String.downcase(phrase)

          if String.starts_with?(lower_text, lower_phrase) do
            len = byte_size(lower_phrase)
            actual = binary_part(text, 0, len)
            rest = binary_part(text, len, byte_size(text) - len)
            {:halt, {actual, term_id, rest}}
          else
            {:cont, :nomatch}
          end
      end
    end)
  end

  defp alphanumeric?(c) do
    (c >= ?a and c <= ?z) or (c >= ?A and c <= ?Z) or (c >= ?0 and c <= ?9) or c == ?_
  end

  # --------------------------------------------------------------------------
  # <.term> — wrap any jargon word in clickable form.
  # --------------------------------------------------------------------------

  attr :id, :string, required: true
  slot :inner_block, required: true

  @doc """
  Renders a clickable jargon term. Click triggers `select_term` on the parent
  LiveView with `phx-value-term=<id>`.
  """
  def term(assigns) do
    ~H"""
    <button
      type="button"
      phx-click="select_term"
      phx-value-term={@id}
      class="text-slate-200 underline decoration-dotted decoration-slate-600 underline-offset-2 hover:decoration-sky-400 hover:text-sky-300 cursor-help bg-transparent border-0 p-0 font-inherit"
    >
      {render_slot(@inner_block)}
    </button>
    """
  end

  # --------------------------------------------------------------------------
  # <.explanation_panel> — pinned at the bottom of the drawer / chat sidebar.
  # --------------------------------------------------------------------------

  attr :selected_term_id, :string, default: nil
  attr :level, :string, default: "intermediate"

  def explanation_panel(assigns) do
    entry = if assigns.selected_term_id, do: lookup(assigns.selected_term_id), else: nil

    body_html =
      if entry do
        text = entry[assigns.level] || "—"

        text
        |> Phoenix.HTML.html_escape()
        |> Phoenix.HTML.safe_to_string()
        |> linkify_html(exclude: assigns.selected_term_id)
        |> Phoenix.HTML.raw()
      else
        ""
      end

    assigns = assign(assigns, entry: entry, body_html: body_html)

    ~H"""
    <div :if={@entry} class="border-t border-slate-700 bg-slate-950/80 p-5 sticky bottom-0">
      <div class="flex justify-between items-baseline mb-3">
        <div>
          <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500 mb-1">explain</div>
          <div class="text-slate-100 text-sm font-medium">{@entry["label"]}</div>
        </div>
        <button
          type="button"
          phx-click="close_term"
          class="text-slate-500 hover:text-slate-200 font-mono text-xs"
        >
          close
        </button>
      </div>

      <div class="flex gap-1 mb-3">
        <%= for {key, label} <- [{"beginner", "BEGINNER"}, {"intermediate", "INTERMEDIATE"}, {"advanced", "ADVANCED"}] do %>
          <button
            type="button"
            phx-click="set_glossary_level"
            phx-value-level={key}
            class={[
              "flex-1 py-1.5 font-mono text-[10px] uppercase tracking-widest rounded transition-colors",
              @level == key && "bg-sky-400/15 text-sky-300 border border-sky-400/40",
              @level != key && "bg-slate-900 text-slate-500 hover:text-slate-300 border border-slate-800"
            ]}
          >
            {label}
          </button>
        <% end %>
      </div>

      <p class="text-slate-200 text-sm leading-relaxed">
        {@body_html}
      </p>
    </div>
    """
  end
end
