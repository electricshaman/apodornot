defmodule ApodornotWebWeb.HealthController do
  @moduledoc """
  Public ``/healthz`` endpoint for Fly's health checks. Bypasses passcode
  gating (see ``ApodornotWebWeb.Plugs.Passcode``).
  """

  use ApodornotWebWeb, :controller

  def show(conn, _params), do: text(conn, "ok")
end
