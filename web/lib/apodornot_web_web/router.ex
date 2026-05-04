defmodule ApodornotWebWeb.Router do
  use ApodornotWebWeb, :router

  pipeline :browser do
    plug :accepts, ["html"]
    plug :fetch_session
    plug ApodornotWebWeb.Plugs.RecentSubmissions
    plug :fetch_live_flash
    plug :put_root_layout, html: {ApodornotWebWeb.Layouts, :root}
    plug :protect_from_forgery
    plug :put_secure_browser_headers
  end

  pipeline :api do
    plug :accepts, ["json"]
  end

  scope "/", ApodornotWebWeb do
    pipe_through :browser

    live "/", UploadLive, :index
    live "/s/:submission_id", ScoreLive, :show
    live "/s/:submission_id/reference", ReferenceLive, :show

    get "/uploads/:filename", UploadController, :show
  end

  # Other scopes may use custom stacks.
  # scope "/api", ApodornotWebWeb do
  #   pipe_through :api
  # end

  # Enable LiveDashboard in development
  if Application.compile_env(:apodornot_web, :dev_routes) do
    # If you want to use the LiveDashboard in production, you should put
    # it behind authentication and allow only admins to access it.
    # If your application does not have an admins-only section yet,
    # you can use Plug.BasicAuth to set up some basic authentication
    # as long as you are also using SSL (which you should anyway).
    import Phoenix.LiveDashboard.Router

    scope "/dev" do
      pipe_through :browser

      live_dashboard "/dashboard", metrics: ApodornotWebWeb.Telemetry
    end
  end
end
