defmodule ApodornotWebWeb.AuthController do
  @moduledoc """
  Login + logout endpoints for the shared-passcode gate.
  See ``ApodornotWebWeb.Plugs.Passcode`` for the auth model.
  """

  use ApodornotWebWeb, :controller

  alias ApodornotWebWeb.Plugs.Passcode

  def new(conn, _params) do
    if Passcode.enabled?() do
      render(conn, :new, error: nil, layout: false)
    else
      # No passcode configured — auth is off entirely; the login page is
      # meaningless. Send them to the upload form.
      redirect(conn, to: "/")
    end
  end

  def create(conn, %{"passcode" => supplied}) do
    if Passcode.matches?(supplied) do
      conn
      |> configure_session(renew: true)
      |> put_session(:authenticated, true)
      |> redirect(to: "/")
    else
      # Same template, with error. Sleep briefly to take the edge off
      # rapid brute-force attempts; not a substitute for a rate limiter
      # but enough friction at the human-typing scale we care about.
      Process.sleep(800)
      render(conn, :new, error: "Passcode didn't match.", layout: false)
    end
  end

  def delete(conn, _params) do
    conn
    |> configure_session(drop: true)
    |> redirect(to: "/login")
  end
end
