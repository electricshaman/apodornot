defmodule ApodornotWeb.ChatBudget do
  @moduledoc """
  Global daily cap on chat turns, enforced via a Redis counter.

  Why a global counter (not per-user): we're operating at invite scale with
  a shared passcode, so identity-aware caps don't apply yet. The owner's
  goal is simply "don't run up the Anthropic bill while I'm sleeping."
  When you graduate to email-allowlist auth, swap this for a per-email key.

  ## Configuration

    - ``:daily_chat_turns_cap`` — integer; 0 or unset disables the cap.

  ## Usage

      case ChatBudget.consume() do
        :ok                       -> proceed_with_chat(...)
        {:over, used, cap}        -> show_banner_and_skip(used, cap)
        :disabled                 -> proceed_with_chat(...)   # cap not configured
      end
  """

  @redis_conn :submission_store_redis
  @key_prefix "apodornot:chat_turns:"
  @ttl_seconds 2 * 86_400  # keep the counter for 2 days so the previous-day key doesn't linger forever

  require Logger

  @doc """
  Increment the day's counter by 1; return ``{:over, used, cap}`` if the
  bump would exceed the cap, ``:ok`` otherwise. Returns ``:disabled`` when
  no cap is configured.

  We INCR first then compare so concurrent requests can't slip past the
  cap. If we go over by N due to in-flight concurrency, that's at most
  ~$0.30 of slop, which we accept.
  """
  def consume do
    case configured_cap() do
      cap when cap <= 0 ->
        :disabled

      cap ->
        key = today_key()

        case Redix.command(@redis_conn, ["INCR", key]) do
          {:ok, used} when used == 1 ->
            # First bump of the day — set TTL so old keys self-expire.
            _ = Redix.command(@redis_conn, ["EXPIRE", key, @ttl_seconds])
            check(used, cap)

          {:ok, used} ->
            check(used, cap)

          {:error, reason} ->
            # Don't fail-closed — Redis hiccup shouldn't take chat down.
            # Log and let the request through; the Anthropic console budget
            # is the hard backstop.
            Logger.warning("ChatBudget INCR failed: #{inspect(reason)}; allowing request")
            :ok
        end
    end
  end

  @doc "Return ``{used, cap}`` for the current day, or ``:disabled``."
  def status do
    case configured_cap() do
      cap when cap <= 0 ->
        :disabled

      cap ->
        used =
          case Redix.command(@redis_conn, ["GET", today_key()]) do
            {:ok, nil} -> 0
            {:ok, n} when is_binary(n) -> String.to_integer(n)
            _ -> 0
          end

        {used, cap}
    end
  end

  defp check(used, cap) when used > cap, do: {:over, used, cap}
  defp check(_used, _cap), do: :ok

  defp configured_cap do
    Application.get_env(:apodornot_web, :daily_chat_turns_cap, 0)
  end

  defp today_key do
    {{y, m, d}, _} = :calendar.universal_time()
    @key_prefix <> "#{y}-#{pad(m)}-#{pad(d)}"
  end

  defp pad(n) when n < 10, do: "0#{n}"
  defp pad(n), do: "#{n}"
end
