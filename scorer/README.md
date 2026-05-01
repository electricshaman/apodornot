# apodornot — Is your image ready for APOD, or not?

Objective astrophotography quality evaluation, grounded in signal processing and the
NASA Astronomy Picture of the Day archive — not LLM guesswork.

`apodornot` measures objective quality metrics on a submitted astrophoto, compares the
results against reference distributions built from ~10,000 APOD images, and produces a
diagnostic scorecard that pinpoints where the image is strong, where it's weak, and what
the weak scores likely indicate about acquisition or processing mistakes.

The measurement pipeline is deterministic. No LLMs in the evaluation loop. An optional
MCP server (A9) exposes the pipeline as tools that any MCP-compatible LLM client can call
to handle the natural-language conversation around results.

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
| A9 | `mcp_server` | MCP tools server (presentation layer) |

## Quick start

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Download a few APOD images to play with (DEMO_KEY is rate-limited; get a real key at api.nasa.gov)
apodornot fetch --start 2024-01-01 --end 2024-01-07 --output apod_archive/

# Evaluate a single image
apodornot evaluate path/to/image.fits

# Score against APOD reference distributions
apodornot score path/to/image.fits --target-type "emission nebula"
```

## Status

This is an active build-out. Run `bd ready` to see the next available work, or `bd list`
to see all tracked tasks.
