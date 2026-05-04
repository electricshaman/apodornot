defmodule ApodornotWeb.SubmissionStore do
  @moduledoc """
  Per-submission persistent state, backed by Redis.

  Keeps the same `put/get/delete` API the rest of the app already uses, but
  the data now survives Phoenix restarts. Each submission is a single Redis
  key with a sliding TTL — refreshed on every read so an actively-used
  submission stays alive past its initial TTL window.

  ## Stored fields per submission

    - `image_basename`        — saved filename (served by UploadController)
    - `image_path`            — full filesystem path on the Python side
    - `target_type`           — explicit target category, or nil for auto
    - `equipment_context`     — free-text notes from the upload form
    - `created_at`            — ISO8601 UTC
    - `scorecard`             — full scorecard map after the pipeline finishes
    - `chat_messages`         — list of `%{"role", "content"}`
    - `last_seen_at`          — refreshed on every read

  ## Configuration

    - `:redis_url`            — defaults to `redis://127.0.0.1:6379`
    - `:submission_ttl_seconds` — defaults to 14 days

  ## Local dev

      colima start                  # one-time per boot
      docker compose up -d redis    # from apodornot_web/

  Stop with `docker compose down`. Data persists in the
  ``apodornot_web_apodornot-redis-data`` volume across container restarts.

  ## Why Redis vs SQLite/Agent

  Same tech as Fly's first-party offering, native TTL means no compaction
  code, single-line of plumbing in the supervisor. Pub/sub stays unused for
  now (Phoenix.PubSub handles single-node fine); reach for Redis pub/sub
  when we go multi-node.
  """

  require Logger

  @conn_name :submission_store_redis
  @key_prefix "submission:"
  @recent_set "apodornot:recent_submissions"

  # ---- supervisor child --------------------------------------------------- #

  def child_spec(_opts) do
    %{
      id: __MODULE__,
      start: {__MODULE__, :start_link, []},
      type: :worker,
    }
  end

  def start_link do
    url = Application.fetch_env!(:apodornot_web, :redis_url)
    Redix.start_link(url, name: @conn_name)
  end

  # ---- public API --------------------------------------------------------- #

  @doc """
  Merge ``fields`` into the submission's stored map. Resets TTL to the
  configured window. Records the submission_id in the global recent-set so
  ``list_recent/1`` can enumerate submissions across the system if needed.
  """
  def put(submission_id, fields) when is_map(fields) do
    existing = get(submission_id)
    merged =
      existing
      |> Map.merge(stringify(fields))
      |> ensure_created_at()
      |> Map.put("last_seen_at", iso8601_now())

    encoded = Jason.encode!(merged)

    {:ok, _} =
      Redix.transaction_pipeline(@conn_name, [
        ["SET", key(submission_id), encoded, "EX", ttl()],
        ["ZADD", @recent_set, unix_now(), submission_id],
      ])

    :ok
  end

  @doc """
  Read the submission. Refreshes TTL on hit (sliding expiry). Returns ``%{}``
  for a missing or expired submission so callers can stay defensive.
  """
  def get(submission_id) do
    case Redix.command(@conn_name, ["GET", key(submission_id)]) do
      {:ok, nil} ->
        %{}

      {:ok, json} when is_binary(json) ->
        # Refresh TTL on read so actively-used submissions stay alive.
        Redix.command(@conn_name, ["EXPIRE", key(submission_id), ttl()])
        case Jason.decode(json) do
          {:ok, map} when is_map(map) -> normalize_keys(map)
          _ -> %{}
        end

      {:error, reason} ->
        Logger.warning("SubmissionStore.get(#{submission_id}) failed: #{inspect(reason)}")
        %{}
    end
  end

  def delete(submission_id) do
    Redix.transaction_pipeline(@conn_name, [
      ["DEL", key(submission_id)],
      ["ZREM", @recent_set, submission_id],
    ])

    :ok
  end

  @doc """
  Return at most ``limit`` of the given ``submission_ids`` (typically the
  visitor's cookie list), filtering out any that have expired. Returns a list
  of `%{id, scorecard, image_basename, ...}` maps in the same order as input.
  """
  def fetch_many(submission_ids, _opts \\ []) do
    submission_ids
    |> Enum.map(fn id ->
      case get(id) do
        m when map_size(m) == 0 -> nil
        m -> Map.put(m, "id", id)
      end
    end)
    |> Enum.reject(&is_nil/1)
  end

  # ---- helpers ----------------------------------------------------------- #

  defp key(id), do: @key_prefix <> id

  defp ttl, do: Application.fetch_env!(:apodornot_web, :submission_ttl_seconds)

  defp unix_now, do: System.system_time(:second)

  defp iso8601_now, do: DateTime.utc_now() |> DateTime.to_iso8601()

  defp ensure_created_at(map) do
    if Map.has_key?(map, "created_at"), do: map, else: Map.put(map, "created_at", iso8601_now())
  end

  defp stringify(map) when is_map(map) do
    Map.new(map, fn {k, v} ->
      key_str = if is_atom(k), do: Atom.to_string(k), else: to_string(k)
      {key_str, v}
    end)
  end

  # Earlier code used atom keys (image_path, target_type, etc.); we now store
  # everything as JSON with string keys. This shim keeps any legacy callers
  # working and ensures the returned map uses string keys consistently.
  defp normalize_keys(map) when is_map(map) do
    Map.new(map, fn {k, v} -> {to_string(k), v} end)
  end
end
