defmodule ApodornotWebWeb.ReferenceLive do
  use ApodornotWebWeb, :live_view

  def mount(%{"submission_id" => id} = params, _session, socket) do
    target_type = Map.get(params, "target_type", "global")

    {:ok,
     socket
     |> assign(submission_id: id, target_type: target_type)
     |> assign_async(:references, fn ->
       url = pipeline_url() <> "/reference"
       resp = Req.get!(url, params: [target_type: target_type])

       case resp.body do
         %{"entries" => entries} = payload ->
           {:ok, %{references: payload, entries: entries}}

         _ ->
           {:error, "unexpected response"}
       end
     end)}
  end

  def render(assigns) do
    ~H"""
    <div class="min-h-screen bg-slate-950 text-slate-100 font-sans px-8 pt-20 pb-16">
      <div class="max-w-6xl mx-auto">
        <div class="mb-8">
          <a href={~p"/s/#{@submission_id}"} class="font-mono text-xs uppercase tracking-widest text-slate-500 hover:text-slate-300">
            ← back to scorecard
          </a>
          <h1 class="text-2xl mt-3">Reference set: <span class="font-mono text-sky-400">{@target_type}</span></h1>
        </div>

        <.async_result :let={refs} assign={@references}>
          <:loading>
            <div class="font-mono text-sm text-slate-500">loading…</div>
          </:loading>
          <:failed :let={err}>
            <div class="text-rose-400">Failed to load reference set: {inspect(err)}</div>
          </:failed>
          <div>
            <div class="font-mono text-xs text-slate-500 mb-6">
              {refs["n"]} APOD entries — your image is being scored against this distribution.
            </div>
            <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
              <a :for={e <- refs["entries"]}
                 href={e["hdurl"] || e["url"]}
                 target="_blank"
                 class="block border border-slate-800 bg-slate-900/30 rounded p-4 hover:border-slate-700 transition-colors">
                <div class="font-mono text-[10px] uppercase tracking-widest text-slate-500 mb-2">
                  {e["date"]}
                </div>
                <div class="text-slate-200 text-sm leading-snug">{e["title"]}</div>
                <div class="font-mono text-[10px] text-slate-600 mt-2 uppercase tracking-wider">
                  {e["category"]}
                </div>
              </a>
            </div>
          </div>
        </.async_result>
      </div>
    </div>
    """
  end

  defp pipeline_url, do: Application.fetch_env!(:apodornot_web, :pipeline_url)
end
