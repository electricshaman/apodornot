defmodule ApodornotWebWeb.Layouts do
  @moduledoc """
  This module holds layouts and related functionality
  used by your application.
  """
  use ApodornotWebWeb, :html

  # Embed all files in layouts/* within this module.
  # The default root.html.heex file contains the HTML
  # skeleton of your application, namely HTML headers
  # and other static content.
  embed_templates "layouts/*"

  @doc """
  Renders your app layout.

  This function is typically invoked from every template,
  and it often contains your application menu, sidebar,
  or similar.

  ## Examples

      <Layouts.app flash={@flash}>
        <h1>Content</h1>
      </Layouts.app>

  """
  attr :flash, :map, required: true, doc: "the map of flash messages"

  attr :current_scope, :map,
    default: nil,
    doc: "the current [scope](https://hexdocs.pm/phoenix/scopes.html)"

  slot :inner_block, required: true

  def app(assigns) do
    ~H"""
    <header class="navbar px-4 sm:px-6 lg:px-8">
      <div class="flex-1">
        <a href="/" class="flex-1 flex w-fit items-center gap-2">
          <img src={~p"/images/logo.svg"} width="36" />
          <span class="text-sm font-semibold">v{Application.spec(:phoenix, :vsn)}</span>
        </a>
      </div>
      <div class="flex-none">
        <ul class="flex flex-column px-1 space-x-4 items-center">
          <li>
            <a href="https://phoenixframework.org/" class="btn btn-ghost">Website</a>
          </li>
          <li>
            <a href="https://github.com/phoenixframework/phoenix" class="btn btn-ghost">GitHub</a>
          </li>
          <li>
            <.theme_toggle />
          </li>
          <li>
            <a href="https://hexdocs.pm/phoenix/overview.html" class="btn btn-primary">
              Get Started <span aria-hidden="true">&rarr;</span>
            </a>
          </li>
        </ul>
      </div>
    </header>

    <main class="px-4 py-20 sm:px-6 lg:px-8">
      <div class="mx-auto max-w-2xl space-y-4">
        {render_slot(@inner_block)}
      </div>
    </main>

    <.flash_group flash={@flash} />
    """
  end

  @doc """
  Shows the flash group with standard titles and content.

  ## Examples

      <.flash_group flash={@flash} />
  """
  attr :flash, :map, required: true, doc: "the map of flash messages"
  attr :id, :string, default: "flash-group", doc: "the optional id of flash container"

  def flash_group(assigns) do
    ~H"""
    <div id={@id} aria-live="polite">
      <.flash kind={:info} flash={@flash} />
      <.flash kind={:error} flash={@flash} />

      <.flash
        id="client-error"
        kind={:error}
        title={gettext("We can't find the internet")}
        phx-disconnected={show(".phx-client-error #client-error") |> JS.remove_attribute("hidden")}
        phx-connected={hide("#client-error") |> JS.set_attribute({"hidden", ""})}
        hidden
      >
        {gettext("Attempting to reconnect")}
        <.icon name="hero-arrow-path" class="ml-1 size-3 motion-safe:animate-spin" />
      </.flash>

      <.flash
        id="server-error"
        kind={:error}
        title={gettext("Something went wrong!")}
        phx-disconnected={show(".phx-server-error #server-error") |> JS.remove_attribute("hidden")}
        phx-connected={hide("#server-error") |> JS.set_attribute({"hidden", ""})}
        hidden
      >
        {gettext("Attempting to reconnect")}
        <.icon name="hero-arrow-path" class="ml-1 size-3 motion-safe:animate-spin" />
      </.flash>
    </div>
    """
  end

  @doc """
  Provides dark vs light theme toggle based on themes defined in app.css.

  See <head> in root.html.heex which applies the theme before page load.
  """
  def theme_toggle(assigns) do
    ~H"""
    <div class="card relative flex flex-row items-center border-2 border-base-300 bg-base-300 rounded-full">
      <div class="absolute w-1/3 h-full rounded-full border-1 border-base-200 bg-base-100 brightness-200 left-0 [[data-theme=light]_&]:left-1/3 [[data-theme=dark]_&]:left-2/3 transition-[left]" />

      <button
        class="flex p-2 cursor-pointer w-1/3"
        phx-click={JS.dispatch("phx:set-theme")}
        data-phx-theme="system"
      >
        <.icon name="hero-computer-desktop-micro" class="size-4 opacity-75 hover:opacity-100" />
      </button>

      <button
        class="flex p-2 cursor-pointer w-1/3"
        phx-click={JS.dispatch("phx:set-theme")}
        data-phx-theme="light"
      >
        <.icon name="hero-sun-micro" class="size-4 opacity-75 hover:opacity-100" />
      </button>

      <button
        class="flex p-2 cursor-pointer w-1/3"
        phx-click={JS.dispatch("phx:set-theme")}
        data-phx-theme="dark"
      >
        <.icon name="hero-moon-micro" class="size-4 opacity-75 hover:opacity-100" />
      </button>
    </div>
    """
  end

  # --------------------------------------------------------------------------
  # apodornot top-bar chrome — used on every authenticated page
  # --------------------------------------------------------------------------

  alias ApodornotWebWeb.RecentMenu

  @doc """
  Fixed top-bar with the recent-submissions dropdown on the left and the
  ``what's new`` + ``sign out`` nav cluster on the right.

  Variants:

    - ``recent_submissions`` — list rendered as the dropdown.
    - ``current_id``         — highlights the active submission.
    - ``left_offset``        — px to push the bar's left edge by (used by
      ScoreLive when the chat sidebar is open or collapsed).
    - ``:left`` / ``:right`` slots — extra content sandwiched between the
      built-in left and right clusters. Use ``:right`` to add page-specific
      status text (e.g. ``vs APOD rosette · n=41``); use ``:left`` to drop in
      a brand link on pages where the body doesn't already say "apodornot".

  The sign-out form is hidden when ``Plugs.Passcode.enabled?/0`` is false
  so dev-mode (no passcode) doesn't tease a feature that doesn't apply.
  """
  attr :recent_submissions, :list, default: []
  attr :current_id, :string, default: nil
  attr :left_offset, :string, default: nil
  attr :authenticated, :boolean,
    default: true,
    doc: "false on public pages — hides signout + recent dropdown"

  slot :left
  slot :right

  def app_chrome(assigns) do
    ~H"""
    <div
      class="fixed top-0 right-0 px-6 py-3 flex items-center justify-between gap-4 z-10 pointer-events-none transition-all"
      style={"left: #{@left_offset || "0"}"}
    >
      <div class="flex items-center gap-4 pointer-events-auto">
        {render_slot(@left)}
        <RecentMenu.recent_menu
          :if={@authenticated}
          items={@recent_submissions}
          current_id={@current_id}
        />
      </div>
      <div class="flex items-center gap-4 pointer-events-auto">
        {render_slot(@right)}
        <a
          href={~p"/changelog"}
          class="font-mono text-[10px] uppercase tracking-widest text-slate-600 hover:text-slate-300"
        >
          what's new
        </a>
        <.signout_form :if={@authenticated and ApodornotWebWeb.Plugs.Passcode.enabled?()} />
      </div>
    </div>
    """
  end

  @doc "POST /logout button styled to match the rest of the chrome nav."
  def signout_form(assigns) do
    ~H"""
    <form method="post" action="/logout" class="m-0">
      <input type="hidden" name="_csrf_token" value={Phoenix.Controller.get_csrf_token()} />
      <button
        type="submit"
        class="font-mono text-[10px] uppercase tracking-widest text-slate-600 hover:text-slate-300 bg-transparent border-0 p-0 cursor-pointer"
      >
        sign out
      </button>
    </form>
    """
  end

  @doc "``apodornot`` brand link, monospace, slate-500."
  def brand_link(assigns) do
    ~H"""
    <a
      href={~p"/"}
      class="font-mono text-xs uppercase tracking-widest text-slate-500 hover:text-slate-300"
    >
      apodornot
    </a>
    """
  end
end
