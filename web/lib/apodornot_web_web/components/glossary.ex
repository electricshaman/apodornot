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

  @doc "Look up a glossary entry by id, returning a map %{label, beginner, intermediate, advanced} or nil."
  def lookup(term_id), do: Map.get(@entries, term_id)

  @doc "Return all glossary keys (useful for tests and unused-term checks)."
  def keys, do: Map.keys(@entries)

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
  # <.explanation_panel> — pinned at the bottom of the drawer.
  # --------------------------------------------------------------------------

  attr :selected_term_id, :string, default: nil
  attr :level, :string, default: "intermediate"

  def explanation_panel(assigns) do
    entry = if assigns.selected_term_id, do: lookup(assigns.selected_term_id), else: nil
    assigns = assign(assigns, entry: entry)

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
        {@entry[@level] || "—"}
      </p>
    </div>
    """
  end
end
