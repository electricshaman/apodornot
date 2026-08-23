# apodornot — Is your image ready for APOD, or not?

Objective astrophotography quality evaluation, grounded in signal processing and the
NASA Astronomy Picture of the Day archive — not LLM guesswork.

`apodornot` measures objective quality metrics on a submitted astrophoto, compares the
results against reference distributions built from APOD archive images, and produces a
diagnostic scorecard that pinpoints where the image is strong, where it's weak, and what
the weak scores likely indicate about acquisition or processing mistakes.

The measurement pipeline is deterministic. **No LLMs in the evaluation loop.** An optional
MCP server (A9) exposes the pipeline as tools that any MCP-compatible LLM client can call
to handle the natural-language conversation around the results.

> ⚠ **Caveat — reference domain is APOD display JPEGs.** APOD distributes finished,
> 8-bit JPEG (and rare PNG/TIFF) display images, not linear master data. The bundled
> reference distributions therefore reflect the look of *processed exports*, including
> their 8x8 DCT artifacts, chroma subsampling, and 8-bit clipping. Several metrics —
> `autocorr_width_px`, `psd_high_band_suppression`, `gradient_ratio`, and the color
> metrics — are biased by this domain. **For comparable scoring, submit an 8-bit JPEG/PNG
> export of your final processed image, not a linear FITS / 16-bit TIFF master.**
> The pipeline detects linear/master inputs at evaluation time and emits a domain-mismatch
> warning on the scorecard, but the underlying reference set hasn't changed.

## Screenshots

<!-- Drop images into docs/screenshots/ and uncomment. See docs/screenshots/README.md for how to capture them. -->
<!--
| Scorecard | Stage detail |
|---|---|
| ![Scorecard — radar plot with per-axis scores and findings](docs/screenshots/scorecard.png) | ![Stage detail — measured metrics against the APOD reference distribution](docs/screenshots/stage-detail.png) |
-->

## Pipeline

| Stage | Module | Purpose |
| ----- | ------ | ------- |
| A0 | `apod_client` | NASA APOD API client + archive download |
| A1 | `image_chars` | Load, normalize, background, source detection, segmentation |
| A2 | `star_field` | Per-star Moffat fits, FWHM/eccentricity field analysis |
| A3 | `noise` | Background noise statistics, PSD shape, autocorrelation |
| A4 | `target_freq` | Radial power spectrum, oversharpening / ringing detection |
| A5 | `gradient` | Gradient and vignetting residuals, color balance |
| A6 | `color` | Star color accuracy, background neutrality, palette detection |
| A7 | `scoring` | Percentile scoring against APOD distributions, scorecard |
| A8 | `archive_pipeline` | Batch processing of the APOD archive, distribution building |
| A9 | `mcp_server` | MCP tools server (presentation layer for LLM clients) |
| —  | `web` | FastAPI streaming HTTP service (presentation layer for LiveView / web UI) |

## Install

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mcp]"
```

The bundled `src/apodornot/data/reference_distributions.json` ships a small seed
distribution. To get a richer reference set, run the full archive pipeline (see below).

## CLI

```bash
# Download APOD entries to apod_archive/YYYY/YYYY-MM-DD.<ext> with JSON sidecars
apodornot fetch --start 2024-01-01 --end 2024-01-31 --output apod_archive

# Run the measurement pipeline on a single image (full structured metrics)
apodornot evaluate path/to/image.fits

# Score an image against the APOD reference distributions, optionally render radar
apodornot score path/to/image.fits --target-type emission_nebula --radar /tmp/radar.png

# Build the reference distributions from the archive
apodornot build-archive --workers 8
apodornot build-distributions

# Run the MCP server (stdio for Claude Desktop / Claude Code)
apodornot-mcp

# Run the FastAPI streaming service for the LiveView frontend
apodornot-web --port 8000
```

## NASA API key

The default is `DEMO_KEY` (30 req/hour, 50/day). Set `NASA_API_KEY` for production:

```bash
export NASA_API_KEY=your-key-from-api.data.nasa.gov
```

Image downloads go to `apod.nasa.gov` directly and do not count against the API
rate limit — only metadata calls do.

## MCP tools

When run with the MCP server, an LLM client gets these tools:

| Tool | Purpose |
| --- | --- |
| `evaluate_image_tool` | Run A1–A6 on an image, return full metrics |
| `score_image_tool` | Percentile-score an image against the APOD reference set |
| `get_stage_detail` | Deep dive on a single stage's results |
| `list_reference_matches` | Enumerate the APOD entries used as the reference |
| `get_submission_history` | Trend-track a user's prior scorecards |
| `get_diagnostic_context` | What a metric means + how to fix a poor score |

The LLM never sees the raw image — only the structured metrics returned by the tools.

## Tests

```bash
pytest                    # full suite (~50s, ~105 tests)
pytest tests/test_apod_client.py    # one stage
```

End-to-end pipeline tests against a real APOD JPEG run automatically when
`apod_archive/2024/2024-01-15.jpg` is present (downloaded via `apodornot fetch`).

## Design docs

- [`docs/design.md`](docs/design.md) — the full project spec: what each stage
  measures, why, and the reference-set caveats.
- [`docs/integration-sketch.md`](docs/integration-sketch.md) — how the scorer,
  the MCP server, and the web frontend fit together.
- [`docs/ui-design-prompt.md`](docs/ui-design-prompt.md) and
  [`docs/ui-liveview-port-prompt.md`](docs/ui-liveview-port-prompt.md) — the
  design intent behind the frontend, kept because the reasoning still explains
  why the UI looks the way it does.

The Phoenix/LiveView frontend lives in [`web/`](../web/).
