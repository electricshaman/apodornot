defmodule ApodornotWebWeb.Plugs.RecentSubmissions do
  @moduledoc """
  Tracks the visitor's recent submission IDs in the Phoenix session.

  Visiting `/s/:submission_id` prepends that id to the list (deduped, capped
  at `@max`). The list is plumbed into LiveView via `:recent_submission_ids`
  in the LV session map so `live_session`-mounted views can read it without
  another round trip.

  Phoenix sessions are already signed cookies under the hood, so there is no
  separate signing concern. ~10 IDs × 24 bytes is well under the 4 KB cookie
  limit.
  """

  import Plug.Conn

  @max 10
  @session_key "recent_submission_ids"

  def init(opts), do: opts

  def call(conn, _opts) do
    existing = get_session(conn, @session_key) || []
    updated = maybe_prepend(existing, conn.path_info)
    put_session(conn, @session_key, updated)
  end

  # /s/:submission_id  →  prepend the id, dedupe, cap.
  defp maybe_prepend(existing, ["s", id | _rest]) when is_binary(id) and id != "" do
    [id | Enum.reject(existing, &(&1 == id))] |> Enum.take(@max)
  end
  defp maybe_prepend(existing, _path), do: existing
end
