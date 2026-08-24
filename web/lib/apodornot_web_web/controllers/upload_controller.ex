defmodule ApodornotWebWeb.UploadController do
  @moduledoc """
  Serves user-uploaded images for the scorecard preview. Two routes:

    * GET /image/:submission_id  — preferred. Tries the local upload dir
      first, falls back to proxying from the pipeline app
      (``$APODORNOT_PIPELINE_URL/image/:submission_id``). The fallback
      makes the preview survive Phoenix redeploys, since Phoenix's /tmp
      gets wiped but the pipeline's /data volume persists.

    * GET /uploads/:filename — kept for backward compatibility with old
      session URLs; basename-only, must live in upload_dir.
  """

  use ApodornotWebWeb, :controller

  require Logger

  @id_re ~r/^[A-Za-z0-9_-]+$/

  def image(conn, %{"submission_id" => submission_id}) do
    cond do
      not Regex.match?(@id_re, submission_id) ->
        conn |> put_status(:bad_request) |> text("invalid submission id")

      (path = local_path(submission_id)) != nil ->
        send_local(conn, path)

      true ->
        proxy_from_pipeline(conn, submission_id)
    end
  end

  def show(conn, %{"filename" => filename}) do
    safe_name = Path.basename(filename)

    if safe_name != filename or String.contains?(safe_name, "/") do
      conn |> put_status(:bad_request) |> text("invalid filename")
    else
      upload_dir = Application.fetch_env!(:apodornot_web, :upload_dir)
      path = Path.join(upload_dir, safe_name)

      cond do
        not File.exists?(path) ->
          # Try the pipeline as a fallback. The basename starts with the
          # submission_id (we changed the prefix scheme to match), so we
          # can extract it and proxy.
          case String.split(safe_name, "_", parts: 2) do
            [submission_id, _] when submission_id != "" ->
              if Regex.match?(@id_re, submission_id) do
                proxy_from_pipeline(conn, submission_id)
              else
                conn |> put_status(:not_found) |> text("not found")
              end

            _ ->
              conn |> put_status(:not_found) |> text("not found")
          end

        not String.starts_with?(Path.expand(path), Path.expand(upload_dir)) ->
          conn |> put_status(:bad_request) |> text("invalid path")

        true ->
          send_local(conn, path)
      end
    end
  end

  # send_file/3 does not set a content type. Without one the browser will not
  # render the image in an <img> tag — it only worked in production because
  # Phoenix and the pipeline run on separate hosts there, so the proxy path
  # below (which does copy the upstream header) was the one being exercised.
  defp send_local(conn, path) do
    conn
    # nil charset: a charset parameter is meaningless on a binary image type.
    |> put_resp_content_type(MIME.from_path(path), nil)
    |> put_resp_header("cache-control", "public, max-age=300")
    |> send_file(200, path)
  end

  defp local_path(submission_id) do
    upload_dir = Application.fetch_env!(:apodornot_web, :upload_dir)

    case File.ls(upload_dir) do
      {:ok, files} ->
        case Enum.find(files, &String.starts_with?(&1, "#{submission_id}_")) do
          nil -> nil
          name -> Path.join(upload_dir, name)
        end

      _ ->
        nil
    end
  end

  defp proxy_from_pipeline(conn, submission_id) do
    url = pipeline_url() <> "/image/" <> submission_id

    case Req.get(url, receive_timeout: 30_000) do
      {:ok, %Req.Response{status: 200, body: body, headers: headers}} ->
        content_type = content_type(headers)

        conn
        |> put_resp_header("content-type", content_type)
        |> put_resp_header("cache-control", "public, max-age=300")
        |> send_resp(200, body)

      {:ok, %Req.Response{status: status}} ->
        Logger.info("UploadController: pipeline image #{submission_id} returned #{status}")
        conn |> put_status(:not_found) |> text("not found")

      {:error, reason} ->
        Logger.warning("UploadController: pipeline fetch failed: #{inspect(reason)}")
        conn |> put_status(:bad_gateway) |> text("upstream error")
    end
  end

  defp content_type(headers) do
    case headers["content-type"] do
      [type | _] -> type
      type when is_binary(type) -> type
      _ -> "application/octet-stream"
    end
  end

  defp pipeline_url, do: Application.fetch_env!(:apodornot_web, :pipeline_url)
end
