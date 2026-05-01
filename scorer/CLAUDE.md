# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->


## Build & Test

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp]"
pytest                              # full suite (~50s)
pytest tests/test_apod_client.py    # one stage
```

## Architecture Overview

apodornot is a deterministic measurement pipeline (A0–A8) plus an MCP server (A9):

- **A0 `apod_client`** — NASA APOD API client + archive download (rate-limited, retrying, batch + incremental, target-filtered)
- **A1 `image_chars`** — Load FITS/TIFF/PNG/JPEG, normalize, SEP background, source detection + classification, multi-scale segmentation, **input-domain classification**
- **A2 `star_field`** — Per-star Moffat fits, FWHM/eccentricity/PA, field-variation pattern classification (clean/uniform_drift/radial/random_seeing), star colors
- **A3 `noise`** — Per-channel stats, PSD shape, autocorrelation width, target SNR
- **A4 `target_freq`** — Windowed FFT → radial PSD, power-law slope, oversharpening/ringing detection
- **A5 `gradient`** — Polynomial gradient fit, radial vignetting model, color balance
- **A6 `color`** — Star color diversity, background neutrality, palette detection (broadband/SHO/HOO/natural)
- **A7 `scoring`** — Same-target → category → global percentile-scoring fallback chain, 5 weighted composite axes, radar chart, diagnostic findings + domain-mismatch warning
- **A8 `archive_pipeline`** — Parallel batch run with per-image JSON cache, prioritized keyword categorizer, per-category quantile distributions + always-present global pool
- **A9 `mcp_server`** — FastMCP server (stdio/SSE) exposing 6 tools: `evaluate_image_tool`, `score_image_tool`, `get_stage_detail`, `list_reference_matches`, `get_submission_history`, `get_diagnostic_context`

## Conventions & Patterns

- **Design doc lives at `docs/design.md`.** It is the source of truth for what the pipeline is supposed to do and *why*. **Update it whenever you change pipeline behavior, add/remove a stage step, change a metric, or learn something the spec didn't anticipate.** Don't let the doc and the code drift apart — when they do, the doc loses authority.
- **No LLMs in the measurement pipeline (A0–A8).** A9 is the only LLM-adjacent boundary, and even there the LLM never sees raw images — only structured metrics. Don't introduce LLM calls into the measurement layer.
- **APOD reference set is JPEG-domain.** Several metrics (autocorr_width, high_band_suppression, gradient_ratio in noise units, all color metrics) are biased by display-format artifacts. A1.7 detects the input domain and A7.5 emits a warning when input is linear (FITS / 16-bit). When adding metrics, consider whether they'll be domain-biased and document it in `docs/design.md`.
- **`sep.set_extract_pixstack(5_000_000)`** is required at startup — SEP's default 300k overflows on nebula-rich frames. `image_chars.extract_sources` calls this and additionally retries at higher thresholds.
- **Test images:** `apod_archive/2024/2024-01-15.jpg` is the standard end-to-end fixture (downloaded via `apodornot fetch`). Many tests `pytest.skip` if it's missing — that's intentional; they still run on a fresh clone after a `fetch`.
- **Use `bd` for tracking, not TodoWrite/TaskCreate** — see beads section above.
