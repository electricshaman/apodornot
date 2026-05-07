defmodule ApodornotWebWeb.Plugs.Passcode do
  @moduledoc """
  Single-shared-passcode gate.

  Reads ``APODORNOT_PASSCODE`` from the runtime config. If the env var is
  unset (or empty), this plug is a no-op — useful for local dev. Otherwise
  every browser-pipeline request needs ``:authenticated`` set in the
  Phoenix session, which the AuthController sets after a successful POST
  to ``/login``.

  ## Always-public paths

  These bypass auth so they work without a session:
    - ``/healthz``  — Fly health check
    - ``/login``    — the gate itself

  ## Why session, not cookie

  Phoenix sessions are signed cookies under the hood, so this is identical
  in security to a hand-rolled signed cookie, but Plug.Conn already
  provides put_session / get_session so we don't write our own crypto.
  """

  import Plug.Conn
  import Phoenix.Controller, only: [redirect: 2]

  @session_key :authenticated
  @public_paths ["healthz", "login", "changelog"]

  def init(opts), do: opts

  def call(conn, _opts) do
    cond do
      not enabled?() -> conn
      first_segment(conn) in @public_paths -> conn
      get_session(conn, @session_key) == true -> conn
      true -> conn |> redirect(to: "/login") |> halt()
    end
  end

  @doc "True if a passcode is configured. False = open access (dev mode)."
  def enabled?, do: configured_passcode() not in [nil, ""]

  @doc "Compare a user-supplied passcode against the configured one in constant time."
  def matches?(supplied) when is_binary(supplied) do
    case configured_passcode() do
      nil -> false
      "" -> false
      expected -> Plug.Crypto.secure_compare(supplied, expected)
    end
  end
  def matches?(_), do: false

  defp configured_passcode do
    Application.get_env(:apodornot_web, :passcode)
  end

  defp first_segment(%Plug.Conn{path_info: [first | _]}), do: first
  defp first_segment(_), do: nil
end
