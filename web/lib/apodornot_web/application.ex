defmodule ApodornotWeb.Application do
  # See https://hexdocs.pm/elixir/Application.html
  # for more information on OTP Applications
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      ApodornotWebWeb.Telemetry,
      {DNSCluster, query: Application.get_env(:apodornot_web, :dns_cluster_query) || :ignore},
      {Phoenix.PubSub, name: ApodornotWeb.PubSub},
      {Task.Supervisor, name: ApodornotWeb.PipelineTaskSup},
      {Registry, keys: :unique, name: ApodornotWeb.SubmissionRegistry},
      ApodornotWeb.SubmissionStore,
      ApodornotWebWeb.Endpoint
    ]

    # See https://hexdocs.pm/elixir/Supervisor.html
    # for other strategies and supported options
    opts = [strategy: :one_for_one, name: ApodornotWeb.Supervisor]
    Supervisor.start_link(children, opts)
  end

  # Tell Phoenix to update the endpoint configuration
  # whenever the application is updated.
  @impl true
  def config_change(changed, _new, removed) do
    ApodornotWebWeb.Endpoint.config_change(changed, removed)
    :ok
  end
end
