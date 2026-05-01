# apodornot UI — React → Phoenix LiveView port prompt

> Hand this to Claude Code (or another agent capable of writing Elixir) along with the React project produced by `docs/ui-design-prompt.md`. The React project is the visual reference; the goal is a LiveView app that achieves the same screens with idiomatic Phoenix patterns from **LiveView 1.1+ (current stable as of mid-2025) / 1.2 (RC April 2026)** — not a transliteration of React patterns.

---

I have a React/Tailwind UI for an astrophotography evaluation tool called **apodornot** (see attached project). I want to port it to **Phoenix LiveView 1.1+** (target 1.2 RC if available) and use the platform's idiomatic real-time primitives — not re-implement React patterns in LiveView. The Python measurement pipeline stays as-is; Phoenix is purely the presentation layer.

## What you're keeping from the React reference

- The aesthetic: dark, observatory/darkroom feel; no generic SaaS patterns; no decorative starfields; sans-serif chrome, monospace metric values.
- The screen flow: upload → stage-by-stage loading → scorecard with radar + axis cards + findings → stage detail drawer → reference comparison view.
- The data contract: the scorecard JSON shape (image, axes, metrics, diagnostics, warnings, reference_n, etc.). See `docs/ui-design-prompt.md` for the full shape — match it.
- The aesthetic rules: percentiles are not grades, `higher_is_better` is respected, radar fixed 0–100 scale, no emojis.

## What you're throwing away

- React component tree, hooks, client state.
- Recharts (or any heavy JS chart lib). The radar chart should be **hand-rolled SVG inside a function component** — five points, polar coords, no client-side state. It's 30 lines of HEEx.
- `useEffect`, polling, fetch loops. Use LiveView's server-driven model.
- Separate JS hook files. With LiveView 1.1's **colocated hooks**, any JS lives in the same `.ex` file as the component that needs it (see below).
- npm tooling beyond what Phoenix ships (esbuild + Tailwind via `mix assets.build`).

## Modern LiveView idioms to use (1.1+)

This is the point of the port. Don't write LiveView like it's 0.18.

- **`Phoenix.LiveView.ColocatedHook`** for any JS hook you genuinely need. JS lives next to the markup that uses it:
  ```elixir
  def my_component(assigns) do
    ~H"""
    <div phx-hook=".SomeHook" id="x">...</div>
    <script :type={Phoenix.LiveView.ColocatedHook} name=".SomeHook">
      export default { mounted() { /* ... */ } }
    </script>
    """
  end
  ```
  The `.SomeHook` leading-dot syntax is 1.1+; it namespaces the hook to the module. Don't write a separate `assets/js/hooks/*.js` file unless the hook is truly app-wide.
- **`assign_async/3` + `<.async_result>`** for the scorecard payload. Don't roll your own loading/failed/ok branching:
  ```elixir
  socket = assign_async(socket, :scorecard, fn -> {:ok, %{scorecard: run_pipeline(path)}} end)

  ~H"""
  <.async_result :let={scorecard} assign={@scorecard}>
    <:loading><.stage_log stages={@streams.stages} /></:loading>
    <:failed :let={_}><.failure_panel /></:failed>
    <.scorecard_view scorecard={scorecard} />
  </.async_result>
  """
  ```
- **`stream/3` + `stream_insert/3`** for the per-stage progress log. Each pipeline event arrives over PubSub and is `stream_insert`ed; the DOM patch is one row. Streams keep zero state on the server.
- **`:for ... :key={...}`** (LiveView 1.1) for any small in-memory list (e.g., axis cards, findings). With `:key`, comprehensions are now change-tracked at the item level — you no longer need a LiveComponent per row to get a granular diff. Reach for streams only for collections that could grow large or live across reconnects.
- **`<.portal>`** (1.1) for the stage detail drawer. Teleports the drawer into `<body>` so it isn't clipped by overflow rules in the scorecard layout, while keeping its `phx-*` events bound to this LiveView. No JS modal library.
- **Native `<dialog>` element** for the drawer's open/close, controlled with `JS.show/hide` and **`JS.ignore_attributes(["open"])`** so LiveView doesn't fight the browser over the `open` attribute.
- **`JS.toggle_class/1`** for any simple show/hide / chevron-rotate / "expand" interactions. No hooks, no Alpine.
- **`Phoenix.LiveView.Upload`** for image ingestion: `allow_upload(socket, :image, accept: ~w(.fits .tif .tiff .png .jpg .jpeg), max_file_size: 200_000_000, auto_upload: true)`. Surface progress with the entry's `progress` field — no JS required.
- **`Phoenix.PubSub`** for fan-out from the pipeline runner GenServer to subscribed LiveViews. Topic per submission: `"submission:" <> id`.
- **(LiveView 1.2 RC, optional)** `Phoenix.LiveView.ColocatedCSS` for any component-scoped CSS that doesn't fit Tailwind utilities. If targeting 1.1, skip this and stay Tailwind-only.

## What NOT to use

- **Don't reach for `LiveComponent`** for stateless rendering. Function components + `:key` give you the same granular diff with much less ceremony. LiveComponents are only justified when state lives there (e.g., a wizard step).
- **Don't write JS hooks** for chart rendering, modal toggling, class manipulation, or "scroll into view." All have idiomatic server-side or `JS.*` equivalents.
- **Don't add npm packages** beyond what `mix phx.new` ships unless you can justify it.
- **Don't put submission state in the socket.** Use a `GenServer` (or DB row) so it survives reconnects. The socket is the rendering surface, not the source of truth.

## Architecture

- **Phoenix LiveView 1.1+** + Tailwind + the `core_components.ex` patterns Phoenix 1.8 ships. No daisyUI, no shadcn-port, no third-party component library. Build the few primitives you need (button, card, drawer-as-portal-with-dialog) as function components.
- **Python pipeline runs as a separate process.** Don't try to embed it. Two acceptable bridges:
  1. **Port to `apodornot evaluate <path> --json --stream-progress`** — preferred. Recommend extending the CLI to emit one JSON-per-line stage event during the run (`{"stage":"A2","status":"done","detail":"fit 98 stars: median FWHM 3.26 px"}`), then a final scorecard JSON object. The Phoenix side reads stdout line-by-line and broadcasts. Keeps the Python contract clean.
  2. Small FastAPI wrapper around `evaluate_image` / `score_evaluation` that Phoenix calls via `Req`. Use this only if the user prefers a long-running Python service over per-request subprocess.
- **`Apodornot.PipelineRunner`** GenServer (or `Task.Supervisor` task) per submission. Owns one pipeline run. Emits stage events via `Phoenix.PubSub` on the submission's topic. Final scorecard returned to the awaiting `assign_async` task.

## Pages / LiveViews

- `ApodornotWeb.UploadLive` (`/`): drag-drop with `allow_upload`, target-type select, on submit creates a submission record + spawns the runner, redirects to score page.
- `ApodornotWeb.ScoreLive` (`/s/:submission_id`): hosts both the loading state and the scorecard. `assign_async` for the scorecard, `stream` for stage events, `<.portal>` + `<dialog>` for the stage detail drawer.
- `ApodornotWeb.ReferenceLive` (`/s/:submission_id/reference`): grid of APOD reference images. Data fetched once via `assign_async`.

## Component sketch

```elixir
def render(assigns) do
  ~H"""
  <.async_result :let={scorecard} assign={@scorecard}>
    <:loading><.stage_log stages={@streams.stages} /></:loading>
    <:failed :let={_}><.failure_panel /></:failed>
    <.scorecard_view scorecard={scorecard} on_axis_click={JS.show(to: "#stage-drawer")} />
  </.async_result>

  <.portal id="drawer-portal" target="body">
    <dialog id="stage-drawer" phx-mounted={JS.ignore_attributes(["open"])}>
      <.stage_detail :if={@selected_stage} stage={@selected_stage} />
    </dialog>
  </.portal>
  """
end

def handle_info({:stage_event, event}, socket) do
  {:noreply, stream_insert(socket, :stages, event)}
end
```

That's the spine. The whole loading screen is a few lines of HEEx plus one `handle_info`. The drawer is one `<dialog>` inside one `<.portal>`.

## Constraints

- **Use Phoenix's built-in primitives** wherever they exist. If you find yourself writing a hook or installing an npm package to do something LiveView 1.1 already does server-side, stop.
- **Tailwind only** for styling — no CSS-in-JS, no styled-components escape. Use the Tailwind setup `mix phx.new` ships.
- **One LiveView per page**, not one giant LiveView with conditional rendering.
- **Submission state lives in a GenServer or DB**, not the socket.
- **Don't recreate the React component tree.** A LiveView app with PubSub + streams + async assigns has a fundamentally different topology than React with hooks. The screens look the same to the user; the code shouldn't look anything alike.

## Out of scope for v1

User accounts, longitudinal history, comments, social. Just one image in, one scorecard out — same as the React version.

Make it feel like a Phoenix 1.8 / LiveView 1.1+ app, not React in HEEx.
