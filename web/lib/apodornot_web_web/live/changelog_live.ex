defmodule ApodornotWebWeb.ChangelogLive do
  @moduledoc """
  Renders the user-facing change log as a vertical timeline.

  Public route — no passcode gate. The list comes from compile-time-parsed
  CHANGELOG.md (see ApodornotWeb.Changelog).
  """
  use ApodornotWebWeb, :live_view

  alias ApodornotWeb.Changelog

  @impl true
  def mount(_params, _session, socket) do
    {:ok,
     socket
     |> assign(:page_title, "What's new")
     |> assign(:releases, Changelog.releases())}
  end

  @impl true
  def render(assigns) do
    ~H"""
    <div class="min-h-screen bg-slate-950 text-slate-100 font-sans">
      <div class="fixed top-0 left-0 right-0 px-6 py-3 flex items-center justify-between z-10 pointer-events-none">
        <a href={~p"/"} class="font-mono text-xs uppercase tracking-widest text-slate-500 hover:text-slate-300 pointer-events-auto">
          apodornot
        </a>
      </div>

      <div class="max-w-3xl mx-auto px-8 pt-24 pb-20">
        <h1 class="text-3xl font-medium mb-2">What's new</h1>
        <p class="text-slate-400 mb-12 text-sm leading-relaxed">
          User-facing changes to apodornot, newest first. Bug fixes invisible to
          you — and infrastructure changes — aren't surfaced here; they live
          in the git log.
        </p>

        <div class="relative">
          <%!-- Timeline spine --%>
          <div class="absolute left-[7px] top-2 bottom-0 w-px bg-sky-400/15"></div>

          <div :for={release <- @releases} class="relative mb-12">
            <%!-- Date marker --%>
            <div class="flex items-center gap-3 mb-4">
              <div class="relative z-10 w-[15px] h-[15px] rounded-full bg-sky-400/20 border-2 border-sky-400 flex-shrink-0"></div>
              <time class="font-mono text-sm font-semibold text-sky-300 tracking-wide">
                {format_date(release.date)}
              </time>
            </div>

            <%!-- Sections --%>
            <div class="ml-8 space-y-6">
              <div :for={section <- release.sections}>
                <h3 class="text-[11px] font-semibold uppercase tracking-widest text-slate-500 mb-2">
                  {section.category}
                </h3>

                <ul class="space-y-2">
                  <li
                    :for={entry <- section.entries}
                    class="text-sm text-slate-300 leading-relaxed pl-5 relative before:content-[''] before:absolute before:left-0 before:top-[9px] before:w-1.5 before:h-1.5 before:rounded-full before:bg-slate-600"
                  >
                    <strong :if={entry.highlight} class="text-slate-100 font-medium">{entry.highlight}</strong>
                    <span :if={entry.highlight && entry.text != ""} class="text-slate-500"> — </span>
                    <span :if={entry.text != ""}>{entry.text}</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        <div :if={@releases == []} class="text-slate-500 italic text-sm">
          No entries yet.
        </div>
      </div>
    </div>
    """
  end

  defp format_date(date_str) do
    case Date.from_iso8601(date_str) do
      {:ok, date} ->
        month = Enum.at(~w(Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec), date.month - 1)
        "#{month} #{date.day}, #{date.year}"

      _ ->
        date_str
    end
  end
end
