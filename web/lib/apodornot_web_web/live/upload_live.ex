defmodule ApodornotWebWeb.UploadLive do
  use ApodornotWebWeb, :live_view

  alias ApodornotWeb.{PipelineRunner, SubmissionStore}
  alias ApodornotWebWeb.RecentMenu

  @target_types ~w(auto rosette orion_nebula horsehead emission_nebula
                  reflection_nebula planetary_nebula galaxy globular_cluster
                  open_cluster supernova_remnant)

  # 50 MB cap. The pipeline can technically handle larger but the Python
  # process holds the full pixel array + segmentation mask + per-stage
  # diagnostics in RAM; a 139MB PNG hit ~18GB resident in testing. Capping
  # at 50MB keeps a small Fly machine viable. APOD reference set is
  # JPEGs averaging 1-3MB so we lose no comparability.
  @max_size 50_000_000
  @max_size_human "50 MB"

  def mount(_params, session, socket) do
    {:ok,
     socket
     |> assign(
       target_types: @target_types,
       target_type: "auto",
       equipment_context: "",
       error: nil,
       max_size_human: @max_size_human,
       recent_submissions: load_recent(session)
     )
     |> allow_upload(:image,
       accept: ~w(.tif .tiff .png .jpg .jpeg),
       max_file_size: @max_size,
       max_entries: 1,
       # Start uploading as soon as the user picks a file, in parallel with
       # them filling in target type / equipment context. Without this, a
       # 100MB+ PNG can sit silently for ~30s after they hit "Evaluate".
       auto_upload: true
     )}
  end

  def handle_event("validate", params, socket) do
    target = Map.get(params, "target_type", socket.assigns.target_type)
    equipment = Map.get(params, "equipment_context", socket.assigns.equipment_context)

    {:noreply,
     assign(socket, target_type: target, equipment_context: equipment, error: nil)}
  end

  def handle_event("submit", _params, socket) do
    case socket.assigns.uploads.image.entries do
      [] ->
        {:noreply, assign(socket, error: "Pick an image first.")}

      _entries ->
        upload_dir = Application.fetch_env!(:apodornot_web, :upload_dir)
        File.mkdir_p!(upload_dir)

        # Match the pipeline's file naming so /image/:submission_id can
        # find the local file on Phoenix without needing to lookup the
        # random suffix.
        submission_id = random_id()

        [{path, _client_name}] =
          consume_uploaded_entries(socket, :image, fn meta, entry ->
            dest = Path.join(upload_dir, "#{submission_id}_#{entry.client_name}")
            File.cp!(meta.path, dest)
            {:ok, {dest, entry.client_name}}
          end)

        target = if socket.assigns.target_type == "auto", do: nil, else: socket.assigns.target_type
        upload_basename = Path.basename(path)

        SubmissionStore.put(submission_id, %{
          image_path: path,
          image_basename: upload_basename,
          target_type: target,
          equipment_context: socket.assigns.equipment_context
        })

        PipelineRunner.start(submission_id, path, target)

        # Use redirect (full HTTP) instead of push_navigate so that
        # Plugs.RecentSubmissions runs during the GET /s/:id and the
        # session cookie picks up the new submission_id. push_navigate
        # uses LiveView in-process navigation and the plug pipeline is
        # skipped, leaving the dropdown unaware of new submissions until
        # the user does a hard reload.
        {:noreply, redirect(socket, to: ~p"/s/#{submission_id}")}
    end
  end

  def handle_event("cancel", %{"ref" => ref}, socket) do
    {:noreply, cancel_upload(socket, :image, ref)}
  end

  def render(assigns) do
    ~H"""
    <div class="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-center p-8 font-sans">
      <div class="fixed top-0 left-0 right-0 px-6 py-3 flex items-center justify-between gap-4 z-10">
        <RecentMenu.recent_menu items={@recent_submissions} />
        <div class="flex items-center gap-4">
          <a
            href={~p"/changelog"}
            class="font-mono text-[10px] uppercase tracking-widest text-slate-600 hover:text-slate-300"
          >
            what's new
          </a>
        <form :if={ApodornotWebWeb.Plugs.Passcode.enabled?()} method="post" action="/logout" class="m-0">
          <input type="hidden" name="_csrf_token" value={Phoenix.Controller.get_csrf_token()} />
          <button
            type="submit"
            class="font-mono text-[10px] uppercase tracking-widest text-slate-600 hover:text-slate-300 bg-transparent border-0 p-0 cursor-pointer"
          >
            sign out
          </button>
        </form>
        </div>
      </div>
      <div class="max-w-2xl w-full">
        <div class="font-mono text-xs uppercase tracking-widest text-slate-500 mb-4">
          apodornot
        </div>
        <h1 class="text-3xl font-medium mb-2">Evaluate an astrophoto</h1>
        <p class="text-slate-400 mb-2">
          Drop a TIFF, PNG, or JPEG export of your finished image. The pipeline measures it against ~100 APOD reference images
          across noise, star quality, gradient, detail, and color.
        </p>
        <p class="text-slate-500 text-sm mb-8">
          <span class="font-mono text-xs uppercase tracking-widest text-slate-600">limit</span>
          {@max_size_human} per upload. The APOD reference set is JPEGs averaging 1–3 MB; if your
          export is larger, downsize the long edge to ~3000–4000 px before uploading.
        </p>

        <form id="upload-form" phx-submit="submit" phx-change="validate" class="space-y-6">
          <label
            phx-drop-target={@uploads.image.ref}
            class="block border border-dashed border-slate-700 rounded p-12 cursor-pointer hover:border-sky-400 transition-colors"
          >
            <.live_file_input upload={@uploads.image} class="sr-only" />
            <div class="text-center">
              <div class="font-mono text-xs uppercase tracking-widest text-slate-500 mb-2">
                drop or click
              </div>
              <div class="text-slate-300">
                {if Enum.empty?(@uploads.image.entries), do: "Choose an image", else: ""}
              </div>
              <div :for={entry <- @uploads.image.entries} class="space-y-2">
                <div class="text-slate-200 font-mono text-sm flex items-center justify-center gap-3">
                  <span>{entry.client_name}</span>
                  <span class="text-slate-500">· {format_size(entry.client_size)}</span>
                  <span :if={entry.progress == 100} class="text-emerald-400">· ready</span>
                  <span :if={entry.progress < 100} class="text-sky-400">· uploading {entry.progress}%</span>
                  <button
                    type="button"
                    phx-click="cancel"
                    phx-value-ref={entry.ref}
                    class="text-rose-400 hover:underline"
                  >
                    remove
                  </button>
                </div>
                <div :if={entry.progress < 100} class="h-1 bg-slate-800 rounded overflow-hidden mx-auto max-w-xs">
                  <div
                    class="h-full bg-sky-400 transition-all duration-150 ease-out"
                    style={"width: #{entry.progress}%"}
                  ></div>
                </div>
              </div>
            </div>
          </label>

          <div :for={entry <- @uploads.image.entries}>
            <div :for={err <- upload_errors(@uploads.image, entry)} class="text-rose-400 text-sm font-mono">
              {error_to_string(err)}
            </div>
          </div>

          <div>
            <label class="block font-mono text-xs uppercase tracking-widest text-slate-500 mb-2">
              target type
            </label>
            <select
              name="target_type"
              class="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 font-mono text-sm focus:outline-none focus:border-sky-400"
            >
              <option :for={t <- @target_types} value={t} selected={t == @target_type}>
                {t}
              </option>
            </select>
          </div>

          <details class="group" phx-mounted={Phoenix.LiveView.JS.ignore_attributes(["open"])}>
            <summary class="font-mono text-xs uppercase tracking-widest text-slate-500 cursor-pointer hover:text-slate-300 select-none flex items-center gap-2">
              <span class="inline-block transition-transform group-open:rotate-90">▸</span>
              additional context — equipment, integration, processing (optional)
            </summary>
            <div class="mt-3 space-y-2">
              <textarea
                name="equipment_context"
                rows="6"
                placeholder={"Telescope: e.g. William Optics Pleiades 68\nCamera: e.g. ZWO ASI2600MC Pro\nMount: e.g. ZWO AM5\nFilters / integration / processing chain (PixInsight, BlurX, etc.)"}
                class="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 font-mono text-xs leading-relaxed focus:outline-none focus:border-sky-400 placeholder:text-slate-700"
              >{@equipment_context}</textarea>
              <p class="text-slate-500 text-xs leading-relaxed">
                Used by the review sidebar to reason about whether a metric reading is plausible for your setup (e.g. a small refractor on a strain-wave mount won't have meaningful vignetting or tracking error).
              </p>
            </div>
          </details>

          <div :if={@error} class="text-rose-400 font-mono text-sm">{@error}</div>

          <button
            type="submit"
            disabled={uploading?(@uploads.image)}
            class="w-full py-3 bg-sky-400 hover:bg-sky-300 text-slate-950 font-medium rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-sky-400 flex items-center justify-center gap-2"
          >
            <span
              :if={uploading?(@uploads.image)}
              class="inline-block w-4 h-4 border-2 border-slate-950 border-t-transparent rounded-full animate-spin"
            ></span>
            {if uploading?(@uploads.image), do: "Uploading…", else: "Evaluate"}
          </button>
        </form>
      </div>
    </div>
    """
  end

  defp error_to_string(:too_large),
    do: "File is over the #{@max_size_human} limit — please downsize the long edge to ~3000–4000 px and re-upload."
  defp error_to_string(:not_accepted), do: "File type not accepted (TIFF, PNG, or JPEG only)."
  defp error_to_string(:too_many_files), do: "Only one file at a time."
  defp error_to_string(other), do: to_string(other)

  defp uploading?(upload_config) do
    Enum.any?(upload_config.entries, fn e -> e.progress > 0 and e.progress < 100 end)
  end

  # Human-readable byte size — KB for small, MB for big.
  defp format_size(bytes) when bytes >= 1_048_576, do: "#{Float.round(bytes / 1_048_576, 1)} MB"
  defp format_size(bytes) when bytes >= 1024,      do: "#{round(bytes / 1024)} KB"
  defp format_size(bytes), do: "#{bytes} B"

  defp random_id do
    :crypto.strong_rand_bytes(16) |> Base.url_encode64(padding: false)
  end

  defp load_recent(session) do
    ids = Map.get(session, "recent_submission_ids", [])
    SubmissionStore.fetch_many(ids)
  end
end
