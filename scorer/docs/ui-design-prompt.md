# apodornot UI design prompt

> Hand this to Claude Design (or any frontend-design agent). It's deliberately constrained — enough to avoid generic SaaS aesthetics, while leaving real design decisions open.

---

I need a frontend for **apodornot**, an astrophotography quality evaluation tool. The backend is a Python measurement pipeline that scores submitted astrophotos against the NASA APOD archive on five quality dimensions and returns structured JSON. I need a single-page web UI that consumes this JSON and presents it well.

## What this is, and what it isn't

This is a **measurement tool for astrophotographers**, not a social/sharing platform. The user is someone who has spent 30 hours capturing one image and wants honest, technical feedback. The aesthetic should feel like a darkroom or observatory tool — precise, dark, data-dense — not a SaaS app, not "AI for X," and absolutely not a starfield background with twinkling animations. Think: Bloomberg Terminal x Linear x a high-end DSO acquisition tool like NINA or PixInsight. Dark mode primary (near-black, not pure black; deep navy or slate works). Sans-serif for chrome, monospace for metric values. Charts and the uploaded image should dominate; chrome should disappear.

## Core flow

1. **Landing / upload.** Drag-and-drop file zone, optional target type dropdown (rosette, orion_nebula, emission_nebula, galaxy, planetary_nebula, star_cluster, widefield, auto-detect). On drop, the image uploads and the user is taken to a loading state.
2. **Loading state.** Stage-by-stage progress: A1 segmentation → A2 stars → A3 noise → A4 frequency → A5 gradient → A6 color → A7 scoring. Each stage flips from pending → running → done with a small detail line ("detected 6,869 sources", "fit 98 stars: median FWHM 3.26 px"). Should feel like watching a real pipeline run, not a fake "AI is thinking" spinner.
3. **Scorecard (the hero view).** See data shape below.
4. **Stage detail drawer.** Click any axis to slide in a panel with the detailed metrics for that stage's underlying calculations (e.g., clicking "Star quality" shows the per-star FWHM scatter, eccentricity vector field, field-position diagnostic).
5. **Reference comparison view.** "What am I being compared to?" — a grid of the APOD images in the reference set (title, date, thumbnail, NASA URL), with a small "your image is at the 70th percentile of this group" header.

## Scorecard layout

This is the most important screen.

- **Top band:** uploaded image preview (large, can click to zoom), filename, and on the right the **overall score** as a single large number (`72/100`) with the reference tier underneath in small caps (`vs APOD ROSETTE · n=33 · 1996–2025`).
- **Domain warning** (if input is FITS/16-bit): a prominent but not alarmist banner above the scores explaining the format mismatch. Amber, not red — it's a caveat, not a failure.
- **Radar chart** of the 5 axes (Star quality / Noise management / Detail resolution / Gradient control / Color calibration), each scored 0–100, axes labeled around the perimeter. Polar grid at 20/40/60/80/100. The fill should be a translucent accent color, not hot.
- **Axis cards** below the radar (5 cards, horizontal): each shows axis name, score, a small horizontal bar showing where the score sits in the 0–100 range with the median (~50) marked, and 2–3 component metrics with their percentiles as small data rows. Clicking opens the stage detail drawer.
- **Findings list** at the bottom: actionable diagnostic text from the backend (e.g., "Star eccentricity is elevated with consistent NW–SE orientation across the field, suggesting periodic error in RA tracking"). Each finding shows the metric it's about. If there are no findings, say "No notable weaknesses" without ornament.

## Backend data shape (this is real, not invented — match it)

```json
{
  "image_path": "rosette.jpg",
  "input_domain": "display",
  "reference_category": "rosette",
  "reference_n": 33,
  "reference_domain": "display",
  "warnings": [],
  "overall_score": 72.0,
  "axes": [
    {
      "axis": "Star quality",
      "score": 55.8,
      "components": [
        {"metric": "median_fwhm_px", "value": 3.26, "percentile": 70, "higher_is_better": false}
      ]
    }
  ],
  "metrics": [
    {"metric": "median_fwhm_px", "value": 3.26, "percentile": 70,
     "higher_is_better": false,
     "quantiles": {"p10": 2.1, "p25": 2.5, "p50": 3.0, "p75": 3.6, "p90": 4.4}}
  ],
  "diagnostics": ["Star eccentricity is elevated..."]
}
```

A separate endpoint returns reference-set entries `[{date, title, url, hdurl, category}]`.

## Constraints

- **No emojis. No stock astronomy imagery.** The user's uploaded image is the imagery.
- **Percentiles are not "scores out of 100" semantically.** Display them clearly as percentile rank, e.g., "70th of 33 references". Don't display them as a grade.
- **`higher_is_better` matters.** For metrics where lower is better (FWHM, gradient_ratio), still show the percentile as 0–100 where 100 = best. The backend already inverts; just respect the field.
- **The radar chart should not exceed 100.** Use a fixed scale; don't auto-fit.
- **Keep the metric values readable.** Monospace, right-aligned in tabular contexts.
- **Use Tailwind + React + shadcn primitives** if you're picking a stack. Recharts for the radar (or a hand-rolled SVG, which would actually be more honest for a 5-axis polar plot — your call).

## Out of scope for v1

User accounts, longitudinal history, comments, social. Just one image in, one scorecard out.

Make something a working astrophotographer would respect.
