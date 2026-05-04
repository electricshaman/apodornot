defmodule ApodornotWebWeb.AuthHTML do
  use ApodornotWebWeb, :html

  attr :error, :string, default: nil

  def new(assigns) do
    ~H"""
    <!DOCTYPE html>
    <html lang="en" class="bg-slate-950">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>apodornot · enter passcode</title>
        <link rel="stylesheet" href={~p"/assets/css/app.css"} />
      </head>
      <body class="min-h-screen bg-slate-950 text-slate-100 font-sans flex items-center justify-center p-8">
        <div class="max-w-sm w-full">
          <div class="font-mono text-xs uppercase tracking-widest text-slate-500 mb-4">
            apodornot
          </div>
          <h1 class="text-2xl font-medium mb-2">Invite passcode</h1>
          <p class="text-slate-400 mb-8 text-sm leading-relaxed">
            This is a small invite-only build. If you have the passcode, enter it below;
            otherwise reach out and I'll share one.
          </p>

          <form method="post" action={~p"/login"} class="space-y-4">
            <input type="hidden" name="_csrf_token" value={get_csrf_token()} />
            <input
              type="password"
              name="passcode"
              autofocus
              autocomplete="current-password"
              placeholder="passcode"
              class="w-full bg-slate-900 border border-slate-700 rounded px-3 py-3 font-mono text-sm focus:outline-none focus:border-sky-400"
            />
            <div :if={@error} class="text-rose-400 font-mono text-xs">{@error}</div>
            <button
              type="submit"
              class="w-full py-3 bg-sky-400 hover:bg-sky-300 text-slate-950 font-medium rounded transition-colors"
            >
              Enter
            </button>
          </form>
        </div>
      </body>
    </html>
    """
  end
end
