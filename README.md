# apodornot

**Is your astrophoto ready for APOD, or not?**

Objective astrophotography quality evaluation, grounded in signal processing and
the NASA Astronomy Picture of the Day archive — not LLM guesswork.

You submit an image. A deterministic measurement pipeline runs seven stages over
it, compares the results against reference distributions built from the APOD
archive, and returns a scorecard showing where the image is strong, where it is
weak, and what the weak scores usually mean about acquisition or processing.

**No LLMs in the evaluation loop.** General-purpose models are bad at judging
astrophotography — they lack calibrated internal standards and will praise a
noisy, oversaturated image as readily as a well-processed one. Everything scored
here is measured. An optional MCP server exposes the measurements as tools so a
model can talk *about* the results without being trusted to produce them.

## Screenshots

<!-- Drop images into web/docs/screenshots/ and uncomment. -->
<!--
| Upload | Scorecard |
|---|---|
| ![Upload — each measurement stage reports in as it completes](web/docs/screenshots/upload.png) | ![Scorecard — radar plot, axis cards, and findings](web/docs/screenshots/scorecard.png) |
-->

## Layout

| Directory | What it is |
|---|---|
| [`scorer/`](scorer/) | The measurement pipeline (Python): stages A0–A9, the CLI, the MCP server, and a FastAPI service. |
| [`web/`](web/) | The frontend (Phoenix/LiveView): upload, live stage progress, scorecard. |

```
  browser  ──upload──>      web      ──multipart POST /evaluate──>   scorer
     ^                  (Phoenix/LV)                                (FastAPI)
     └───── LiveView ────────┴───────────── SSE: stage, error, done ─────┘
```

The frontend forwards uploaded bytes to the scorer over multipart, so the two
never need a shared filesystem, and streams each stage event straight to the
browser as it arrives. All image analysis happens in `scorer/`; `web/` is
presentation only.

They live in one repository because they are one product: the frontend cannot
run without the scorer, and an API change has to land in the same commit as the
code that consumes it.

## The pipeline

| Stage | Module | What it measures |
| ----- | ------ | ---------------- |
| A0 | `apod_client` | NASA APOD API client and archive download |
| A1 | `image_chars` | Load, normalize, background, source detection, segmentation |
| A2 | `star_field` | Per-star Moffat fits, FWHM and eccentricity across the field |
| A3 | `noise` | Background noise statistics, PSD shape, autocorrelation |
| A4 | `target_freq` | Radial power spectrum, oversharpening and ringing detection |
| A5 | `gradient` | Gradient and vignetting residuals, colour balance |
| A6 | `color` | Star colour accuracy, background neutrality, palette detection |
| A7 | `scoring` | Percentile scoring against the APOD distributions |
| A8 | `archive_pipeline` | Batch processing of the archive, distribution building |
| A9 | `mcp_server` | MCP tools server |

> ⚠ **The reference set is APOD display JPEGs.** APOD publishes finished 8-bit
> exports, not linear masters, so the bundled distributions carry the look of
> processed images — DCT artifacts, chroma subsampling, 8-bit clipping.
> `autocorr_width_px`, `psd_high_band_suppression`, `gradient_ratio`, and the
> colour metrics are biased by this. **Submit an 8-bit JPEG or PNG export of
> your finished image, not a FITS or 16-bit TIFF master.** The pipeline detects
> linear input and warns on the scorecard, but the reference set is unchanged.

## Running it

Both halves, in two terminals. Details in [`scorer/README.md`](scorer/README.md)
and [`web/README.md`](web/README.md).

```bash
# scorer — measurement pipeline on :8000
cd scorer
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp,web]"
apodornot-web --port 8000
```

```bash
# web — Phoenix frontend on :4000
cd web
mise install
mix setup
docker compose up -d      # redis, for submission state
mix phx.server
```

The scorer is useful on its own — it has a CLI and an MCP server, and needs no
frontend:

```bash
apodornot fetch --start 2024-01-15 --end 2024-01-15 --output apod_archive
apodornot score apod_archive/2024/2024-01-15.jpg --target-type emission_nebula
apodornot-mcp                    # stdio MCP server
```

Requires Python 3.11–3.13. A NASA API key is optional — `DEMO_KEY` works at 30
requests/hour; set `NASA_API_KEY` for more. Image downloads come from
`apod.nasa.gov` directly and do not count against the limit.

## Tests

```bash
cd scorer && pytest      # 149 tests
cd web && mix test       #  28 tests
```

Some scorer tests want a real APOD image and skip without one. Fetch the
fixture they look for with:

```bash
cd scorer && apodornot fetch --start 2024-01-15 --end 2024-01-15 --output apod_archive
```

## Design docs

- [`scorer/docs/design.md`](scorer/docs/design.md) — the full spec: what each
  stage measures, why, and the reference-set caveats.
- [`scorer/docs/integration-sketch.md`](scorer/docs/integration-sketch.md) — how
  the scorer, MCP server, and frontend fit together.

## License

MIT.
