defmodule ApodornotWebWeb.UploadController do
  @moduledoc """
  Serves user-uploaded images out of APODORNOT_UPLOAD_DIR for the scorecard
  preview. Path-traversal-safe: only basenames allowed, file must live in the
  configured upload directory.
  """

  use ApodornotWebWeb, :controller

  def show(conn, %{"filename" => filename}) do
    safe_name = Path.basename(filename)

    if safe_name != filename or String.contains?(safe_name, "/") do
      conn |> put_status(:bad_request) |> text("invalid filename")
    else
      upload_dir = Application.fetch_env!(:apodornot_web, :upload_dir)
      path = Path.join(upload_dir, safe_name)

      cond do
        not File.exists?(path) ->
          conn |> put_status(:not_found) |> text("not found")

        not String.starts_with?(Path.expand(path), Path.expand(upload_dir)) ->
          conn |> put_status(:bad_request) |> text("invalid path")

        true ->
          send_file(conn, 200, path)
      end
    end
  end
end
