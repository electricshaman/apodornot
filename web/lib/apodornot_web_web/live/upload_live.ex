defmodule ApodornotWebWeb.UploadLive do
  use ApodornotWebWeb, :live_view

  alias ApodornotWeb.{PipelineRunner, SubmissionStore}

  @target_types ~w(auto rosette orion_nebula horsehead emission_nebula
                  reflection_nebula planetary_nebula galaxy globular_cluster
                  open_cluster supernova_remnant)

  @max_size 200_000_000

  def mount(_params, _session, socket) do
    {:ok,
     socket
     |> assign(
       target_types: @target_types,
       target_type: "auto",
       equipment_context: "",
       error: nil
     )
     |> allow_upload(:image,
       accept: ~w(.fits .fit .tif .tiff .png .jpg .jpeg),
       max_file_size: @max_size,
       max_entries: 1,
       auto_upload: false
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

        [{path, _client_name}] =
          consume_uploaded_entries(socket, :image, fn meta, entry ->
            dest = Path.join(upload_dir, "#{random_id()}_#{entry.client_name}")
            File.cp!(meta.path, dest)
            {:ok, {dest, entry.client_name}}
          end)

        submission_id = random_id()
        target = if socket.assigns.target_type == "auto", do: nil, else: socket.assigns.target_type
        upload_basename = Path.basename(path)

        SubmissionStore.put(submission_id, %{
          image_path: path,
          image_basename: upload_basename,
          target_type: target,
          equipment_context: socket.assigns.equipment_context
        })

        PipelineRunner.start(submission_id, path, target)

        {:noreply,
         socket
         |> push_navigate(to: ~p"/s/#{submission_id}")}
    end
  end

  def handle_event("cancel", %{"ref" => ref}, socket) do
    {:noreply, cancel_upload(socket, :image, ref)}
  end

  def render(assigns) do
    ~H"""
    <div class="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-center p-8 font-sans">
      <div class="max-w-2xl w-full">
        <div class="font-mono text-xs uppercase tracking-widest text-slate-500 mb-4">
          apodornot
        </div>
        <h1 class="text-3xl font-medium mb-2">Evaluate an astrophoto</h1>
        <p class="text-slate-400 mb-8">
          Drop a FITS, TIFF, PNG, or JPEG. The pipeline measures it against ~100 APOD reference images
          across noise, star quality, gradient, detail, and color.
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
              <div :for={entry <- @uploads.image.entries} class="text-slate-200 font-mono text-sm">
                {entry.client_name}
                <span class="text-slate-500">· {round(entry.client_size / 1024)} KB</span>
                <button
                  type="button"
                  phx-click="cancel"
                  phx-value-ref={entry.ref}
                  class="ml-3 text-rose-400 hover:underline"
                >
                  remove
                </button>
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
            class="w-full py-3 bg-sky-400 hover:bg-sky-300 text-slate-950 font-medium rounded transition-colors"
          >
            Evaluate
          </button>
        </form>
      </div>
    </div>
    """
  end

  defp error_to_string(:too_large), do: "file too large"
  defp error_to_string(:not_accepted), do: "file type not accepted"
  defp error_to_string(:too_many_files), do: "only one file at a time"
  defp error_to_string(other), do: to_string(other)

  defp random_id do
    :crypto.strong_rand_bytes(16) |> Base.url_encode64(padding: false)
  end
end
