# This file is responsible for configuring your application
# and its dependencies with the aid of the Config module.
#
# This configuration file is loaded before any dependency and
# is restricted to this project.

# General application configuration
import Config

config :apodornot_web,
  generators: [timestamp_type: :utc_datetime]

# Register FITS file type so LiveView's allow_upload accepts .fits / .fit.
config :mime, :types, %{
  "image/fits" => ["fits", "fit", "fts"]
}

# Configure the endpoint
config :apodornot_web, ApodornotWebWeb.Endpoint,
  url: [host: "localhost"],
  adapter: Bandit.PhoenixAdapter,
  render_errors: [
    formats: [html: ApodornotWebWeb.ErrorHTML, json: ApodornotWebWeb.ErrorJSON],
    layout: false
  ],
  pubsub_server: ApodornotWeb.PubSub,
  live_view: [signing_salt: "Lj7wj4Xh"]

# Configure esbuild (the version is required)
config :esbuild,
  version: "0.25.4",
  apodornot_web: [
    args:
      ~w(js/app.js --bundle --target=es2022 --outdir=../priv/static/assets/js --external:/fonts/* --external:/images/* --alias:@=.),
    cd: Path.expand("../assets", __DIR__),
    env: %{"NODE_PATH" => [Path.expand("../deps", __DIR__), Mix.Project.build_path()]}
  ]

# Configure tailwind (the version is required)
config :tailwind,
  version: "4.1.12",
  apodornot_web: [
    args: ~w(
      --input=assets/css/app.css
      --output=priv/static/assets/css/app.css
    ),
    cd: Path.expand("..", __DIR__)
  ]

# Configure Elixir's Logger
config :logger, :default_formatter,
  format: "$time $metadata[$level] $message\n",
  metadata: [:request_id]

# Use Jason for JSON parsing in Phoenix
config :phoenix, :json_library, Jason

# Import environment specific config. This must remain at the bottom
# of this file so it overrides the configuration defined above.
import_config "#{config_env()}.exs"
