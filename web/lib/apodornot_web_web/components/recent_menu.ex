defmodule ApodornotWebWeb.RecentMenu do
  @moduledoc """
  Top-bar dropdown listing the visitor's recent submissions.

  Items come from `ApodornotWeb.SubmissionStore.fetch_many/1` so they already
  carry the score, target type, and timestamps. Fetched in the host
  LiveView's `mount/3` from the session and passed in as an attr.
  """

  use Phoenix.Component
  use ApodornotWebWeb, :verified_routes

  attr :items, :list, required: true
  attr :current_id, :string, default: nil

  def recent_menu(assigns) do
    ~H"""
    <details :if={@items != []} class="relative font-mono text-xs">
      <summary class="cursor-pointer list-none text-slate-500 hover:text-slate-300 select-none">
        recent ({length(@items)})
      </summary>
      <div class="absolute left-0 mt-2 w-72 bg-slate-900 border border-slate-800 rounded shadow-xl py-1 max-h-96 overflow-y-auto">
        <a
          :for={s <- @items}
          href={~p"/s/#{s["id"]}"}
          class={[
            "block px-3 py-2 hover:bg-slate-800 transition-colors",
            s["id"] == @current_id && "bg-slate-800"
          ]}
        >
          <div class="text-slate-200 truncate">{s["image_basename"] || s["id"]}</div>
          <div class="flex gap-3 text-[10px] text-slate-500 mt-0.5">
            <span :if={overall_pct(s)}>{overall_pct(s)}</span>
            <span :if={s["target_type"]}>{s["target_type"]}</span>
            <span class="ml-auto">{relative_time(s["last_seen_at"] || s["created_at"])}</span>
          </div>
        </a>
      </div>
    </details>
    """
  end

  defp overall_pct(%{"scorecard" => %{"overall" => v}}) when is_number(v),
    do: "#{round(v)}/100"
  defp overall_pct(_), do: nil

  defp relative_time(nil), do: ""
  defp relative_time(iso) when is_binary(iso) do
    case DateTime.from_iso8601(iso) do
      {:ok, dt, _} ->
        diff = DateTime.diff(DateTime.utc_now(), dt, :second)
        cond do
          diff < 60      -> "just now"
          diff < 3600    -> "#{div(diff, 60)}m"
          diff < 86_400  -> "#{div(diff, 3600)}h"
          diff < 604_800 -> "#{div(diff, 86_400)}d"
          true           -> "#{div(diff, 604_800)}w"
        end
      _ -> ""
    end
  end
end
