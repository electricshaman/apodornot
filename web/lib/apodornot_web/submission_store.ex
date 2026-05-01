defmodule ApodornotWeb.SubmissionStore do
  @moduledoc """
  In-process key-value store for per-submission metadata that needs to flow
  between LiveViews (UploadLive → ScoreLive → ChatRunner) without making it
  a URL parameter.

  Currently holds:
    - image_path        — the saved upload path on disk (Python service reads it)
    - target_type       — explicit target category (or nil = auto-detect)
    - equipment_context — free-form text the user entered on the upload form,
                          flowed into the chat system prompt

  Backed by an `Agent`. Single-node only — fine for v1. For Fly multi-machine
  this would move to ETS+PG (or Redis) keyed by submission_id. Submissions
  are not pruned currently; restart wipes.
  """

  use Agent

  def start_link(_opts) do
    Agent.start_link(fn -> %{} end, name: __MODULE__)
  end

  def put(submission_id, fields) when is_map(fields) do
    Agent.update(__MODULE__, fn store ->
      Map.update(store, submission_id, fields, &Map.merge(&1, fields))
    end)
  end

  def get(submission_id) do
    Agent.get(__MODULE__, &Map.get(&1, submission_id, %{}))
  end

  def delete(submission_id) do
    Agent.update(__MODULE__, &Map.delete(&1, submission_id))
  end
end
