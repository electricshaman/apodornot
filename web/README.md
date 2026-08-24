# apodornot-web

**The web frontend for [apodornot](../) — upload an astrophoto, watch it get measured, read the scorecard.**

A Phoenix/LiveView app in front of the apodornot measurement pipeline. You upload an image; the Python service streams stage-by-stage progress back over SSE; the page fills in as each stage completes and ends on a diagnostic scorecard.

The evaluation itself happens in the Python pipeline. This repository is purely the presentation layer — no image analysis lives here.

## Screenshots

See the [root README](../README.md#screenshots).

## How it fits together

```
  browser  ──upload──>  apodornot-web  ──multipart POST /evaluate──>  apodornot
     ^                  (Phoenix/LV)                                  (FastAPI)
     └───── LiveView ────────┴────────────── SSE: stage, error, done ──────┘
```

The Phoenix app forwards the uploaded bytes to the pipeline over multipart, so
the two services need no shared filesystem. Each SSE event the pipeline emits
is pushed straight to the browser over the LiveView socket.

| Route | View | Purpose |
|---|---|---|
| `/` | `UploadLive` | Upload an image, watch stages stream in |
| `/s/:submission_id` | `ScoreLive` | Scorecard: radar, axis cards, findings |
| `/s/:submission_id/reference` | `ReferenceLive` | The APOD entries used as the reference set |
| `/changelog` | `ChangelogLive` | Release notes, parsed from CHANGELOG.md |
| `/healthz` | `HealthController` | Health check |

## Requirements

- Erlang 28 and Elixir 1.19 (pinned in `mise.toml` — run `mise install`)
- A running [scorer](../scorer/) pipeline service (`apodornot-web --port 8000`)
- Redis, for submission state (`docker compose up -d`)

## Run it

```bash
mise install
mix setup

docker compose up -d          # redis on 127.0.0.1:6379
mix phx.server                # http://localhost:4000
```

The pipeline service has to be running too — from the apodornot checkout:

```bash
apodornot-web --port 8000
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `APODORNOT_PIPELINE_URL` | `http://127.0.0.1:8000` | Where the Python pipeline lives |
| `SECRET_KEY_BASE` | — | Required in production; generate with `mix phx.gen.secret` |
| `PHX_HOST` | `example.com` | Public hostname in production |
| `REDIS_URL` | `redis://127.0.0.1:6379` | Submission state |

Development and test use the checked-in `secret_key_base` from `config/dev.exs`
and `config/test.exs`. Those are local-only values that sign cookies against a
throwaway database; production reads the secret from the environment and refuses
to boot without it.

## Tests

```bash
mix test
```

## Deployment

Built as an OTP release (`mix release`), configured at runtime from the
environment via `config/runtime.exs`. Deployment configuration is intentionally
not published in this repository.
