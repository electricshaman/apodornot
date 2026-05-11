# apodornot — Is your image ready for APOD, or not?

## Full Project Spec (Rev 2 — post-implementation revision)

## Instructions

Use BEADS to break this project spec down into discrete, sequenced implementation tasks. The spec is organized into tasks A0–A9 with sub-tasks (A0.1, A1.3, etc.) — use that structure as your starting point for decomposition. Implement each bead fully before moving to the next. Start with A0, A1, and A2 as specified in the implementation notes at the bottom.

---

## Project Vision

Build a Python tool that evaluates amateur astrophotography images for quality, providing objective, actionable feedback grounded in signal processing and astronomical image analysis — not AI guesswork.

The benchmark for "good" is the NASA Astronomy Picture of the Day (APOD) archive — roughly 10,000+ expert-curated images spanning 30 years. The tool runs a multi-stage measurement pipeline against a submitted image, compares the resulting metrics against reference distributions built from the APOD archive, and produces a diagnostic scorecard showing where the image is strong, where it's weak, and what the weak scores likely indicate about acquisition or processing mistakes.

General-purpose AI models are terrible at evaluating astrophotography because they lack calibrated internal standards — they praise noisy, oversaturated images the same way they praise well-calibrated ones. This tool solves that by measuring objective quality metrics via signal processing and comparing against real reference data. No LLMs in the evaluation loop.

### Reference Set Caveat

**The APOD reference set consists of finished display-format images — overwhelmingly 8-bit JPEGs, with rare PNG or TIFF.** These images carry the fingerprint of their format: 8x8 DCT block artifacts, chroma subsampling, and 8-bit clipping. The reference distributions therefore reflect display-domain characteristics, not the noise profile of linear astronomical master data. Several metrics are biased by format rather than photographic quality: `autocorr_width_px` (JPEG block correlation inflates this), `psd_high_band_suppression` (DCT artifacts add high-frequency energy), `gradient_ratio` in noise-floor units (JPEG quantization alters the noise floor), and the color metrics (chroma subsampling degrades color fidelity). A linear 32-bit FITS submission will score misleadingly well against this reference because it lacks the JPEG fingerprint, not because it is necessarily better-processed. The pipeline must detect and flag this domain mismatch (see A1.7, A7.5).

## Architecture Overview

The pipeline has 7 analysis stages (A1–A7) plus a dedicated APOD API client (A0), a reference archive pipeline (A8), and an MCP server (A9). Each stage reads from upstream outputs and produces a structured result object. The same pipeline runs against both submitted images and APOD reference images (with reference results cached for reuse).

The pipeline should be designed as composable, independently testable modules. Each stage is its own module. A top-level orchestrator runs them in sequence and collects results.

### Dependency Summary

Core: `astropy`, `photutils`, `sep`, `numpy`, `scipy`, `scikit-image`, `matplotlib`, `Pillow`

Optional for performance: `numba` (JIT for any bottleneck inner loops identified during profiling)

For APOD archive ingestion: `requests` (NASA APOD API)

All pip-installable, nothing exotic.

### SEP Configuration

SEP's default pixel-stack size (300,000) overflows on highly nebulous frames where source detection floods the working buffer. At pipeline startup, call `sep.set_extract_pixstack(5_000_000)`. If extraction still fails at the configured threshold, retry at progressively higher thresholds (e.g., 7σ, 10σ) before giving up. This applies everywhere `sep.extract` is called (A1.4, A8.1).

## Task Index

- **A0** — APOD API Client
- **A1** — Image Characterization and Segmentation
- **A2** — Star Field Analysis
- **A3** — Noise Characterization
- **A4** — Frequency Domain Analysis of Target Structure
- **A5** — Gradient and Calibration Assessment
- **A6** — Color Analysis
- **A7** — Scoring and Comparison
- **A8** — Reference Archive Pipeline (batch processing, caching, categorization)
- **A9** — MCP Server and LLM Conversation Layer

### Pipeline Boundary

A0–A8 form the **measurement pipeline**. Everything here is deterministic signal processing and statistics — no LLMs, no ambiguity. The output of A7 is a structured scorecard of raw metrics, percentile rankings, and diagnostic flags. This boundary is critical: the measurement pipeline must produce correct, reproducible results regardless of what happens downstream.

A9 is the **presentation layer for LLM clients**, implemented as an MCP server. It exposes the measurement pipeline as tools that any MCP-compatible LLM client (Claude, a custom chat UI, etc.) can call. The LLM never sees the raw image — only structured metrics, scores, and flags returned by tool calls. This is the explicit contract: A9 provides the tools, the LLM client explains and discusses results that A0–A8 measured. The conversation layer doesn't live in the codebase — it lives in whatever LLM client connects to the MCP server.

A separate **HTTP/SSE presentation layer** (`apodornot.web`, a FastAPI service — see `docs/integration-sketch.md`) is the canonical interface for non-LLM UI clients (the Phoenix LiveView frontend, future React clients, etc.). Both A9 and `apodornot.web` are thin facades over the same `evaluate_image` and `score_evaluation` functions — the wire formats differ (MCP tool calls vs SSE event stream) but the underlying measurement is identical. New pipeline capabilities should be exposed through both surfaces.

---

## A0 — APOD API Client

**Module:** `apod_client.py`

**Dependencies:** `requests`

**API endpoint:** `https://api.nasa.gov/planetary/apod`

**Key parameters:**
- `api_key` — NASA provides free API keys at `https://api.data.nasa.gov/`. `DEMO_KEY` works for testing but is rate-limited (30 req/hr).
- `date` — single date in YYYY-MM-DD format
- `start_date` / `end_date` — date range query for batch retrieval
- `thumbs=True` — returns thumbnail URL for video entries

**Response fields:** `date`, `title`, `explanation`, `url` (image URL), `hdurl` (high-res image URL), `media_type` ("image" or "video"), `copyright`.

### Steps

**A0.1 — API interaction layer.** Build request/response handling with rate limiting, retry logic, and API key management.

**A0.2 — Batch archive download.** Iterate date ranges from APOD start (1995-06-16) to present. Respect rate limits — use the API key, add polite delays between requests. Download actual image files (prefer `hdurl` when available, fall back to `url`). Store images in a structured directory (e.g., `apod_archive/YYYY/YYYY-MM-DD.jpg`).

**A0.3 — Metadata storage.** Store metadata (title, explanation, date, copyright, media_type) alongside each image as JSON sidecar files.

**A0.4 — Filtering and skipping.** Skip image download for entries where `media_type` is "video", but still write a `.video.json` sidecar so incremental scans don't re-process those dates. Not all APODs are astrophotos — some are illustrations, diagrams, artist renderings, or solar system photos. Download everything but flag entries for later categorization in A8.3.

**A0.5 — Resilience and incremental updates.** Handle failures gracefully — network errors, missing images, API outages. Support resuming an interrupted download. Support incremental updates — check the latest date in the local archive and only fetch new entries.

**A0.6 — Target-filtered fetch.** Support fetching only APOD entries that match a specific target. Walk archive metadata in 90-day chunks, regex-filter on title (preferred) or title+explanation, download just the matching images. This enables workflows like "get every APOD that imaged the Rosette Nebula" without downloading the full 10,000-image archive. Useful for building same-target reference sets (see A7.1) and for rapid prototyping against a specific object class.

---

## A1 — Image Characterization and Segmentation

**Dependencies:** `astropy` (FITS I/O, WCS/header parsing), `scikit-image` (TIFF/PNG/JPEG I/O, color space conversion), `sep` (background estimation, source extraction), `numpy` (array ops, normalization), `Pillow` (fallback I/O)

**Input:** Raw astrophoto — FITS, TIFF, PNG, or JPEG, full resolution.

### Steps

**A1.1 — Load and normalize.** Read the image, detect bit depth (8, 16, 32-bit), convert to 32-bit float normalized to [0,1]. Preserve original bit depth as metadata — someone submitting 8-bit JPEG has already lost data, which is itself a quality signal. If FITS, extract available header metadata via `astropy.io.fits`: pixel scale (arcsec/px), exposure time, gain, instrument info, WCS if present. If color, split channels and generate a luminance image for structural analysis (weighted average: 0.2126*R + 0.7152*G + 0.0722*B). Store both luminance and color channels.

**A1.2 — Basic image characterization.** Dimensions, aspect ratio, dynamic range utilization (histogram analysis — what fraction of the available range is actually used, whether the histogram is clipped at either end).

**A1.3 — Background modeling.** Use `sep.Background` with a mesh-based approach — divide into a grid (64x64 or 128x128 cells), compute sigma-clipped median per cell to reject stars and target signal, interpolate smoothly. Output is a background map (estimated sky level per pixel) and an RMS map (local noise estimate per pixel). The RMS map matters because noise isn't uniform — it varies with background level and any residual vignetting or calibration issues.

**A1.4 — Source detection.** Run `sep.extract` on the background-subtracted image with a detection threshold of ~5 sigma relative to the local RMS. Ensure `sep.set_extract_pixstack(5_000_000)` has been called (see SEP Configuration above). If extraction fails due to pixstack overflow even at the raised limit, retry at higher thresholds (7σ, 10σ). Produces a raw catalog: positions (x, y), fluxes, initial shape parameters (semi-major axis `a`, semi-minor axis `b`, position angle `theta`). This catches everything — stars, hot pixels, cosmic rays, nebular knots, small galaxies.

**A1.5 — Source classification.** Separate detected sources into categories using the shape parameters from SEP:
- **Stars:** point-like, size consistent with the median PSF. Stars cluster tightly in size-space.
- **Extended sources:** significantly larger than the PSF (galaxies, bright nebular knots).
- **Artifacts:** smaller than PSF or abnormal shape characteristics (hot pixels, cosmic rays).

**A1.6 — Target segmentation.** Subtract background, mask detected point sources, then detect diffuse structure using empirical noise estimation at each smoothing scale. Apply a series of Gaussian kernels at increasing sigmas. **Do not use the analytical Gaussian noise-reduction formula** (1/(2σ√π) for white noise) — it underestimates effective noise once real diffuse signal is present, causing the segmentation to classify the entire frame as target at large smoothing scales. Instead, at each scale: compute the sigma-clipped MAD (median absolute deviation) of the smoothed image, then threshold at 3σ above the smoothed median. Cap the largest smoothing scale at `min(H, W) / 16` so segmentation works on both small test images and large astro frames. Output a segmentation mask labeling each pixel as: background, target signal, or source.

**A1.7 — Image domain classification.** Classify the input image as either **display** (finished JPEG/PNG intended for viewing) or **linear** (FITS or high-bit-depth TIFF preserving linear pixel values from calibration). Classification uses file format and bit depth: JPEG → display; 8-bit PNG → display; FITS → linear; 16/32-bit TIFF → linear; 16-bit PNG → linear. Store the domain tag in image metadata. This tag is consumed by A7.5 to flag domain mismatches against the APOD reference set.

### Output

A structured object (dataclass or dict) containing:
- Background map (2D array)
- RMS map (2D array)
- Classified source catalog (stars, extended, artifacts — each with positions, fluxes, shapes)
- Segmentation mask (2D integer array)
- Luminance image (2D array)
- Color channel arrays (3D array if color)
- Image metadata (bit depth, dimensions, FITS headers if available, dynamic range stats, **image domain tag**)

---

## A2 — Star Field Analysis

**Dependencies:** `photutils` (PSF fitting framework, `Moffat2DKernel`, model fitting), `scipy` (`scipy.optimize.curve_fit` for iterative Moffat profile fitting, `scipy.spatial` for field position analysis), `numpy` (array slicing, statistics), `matplotlib` (diagnostic star field maps)

**Input:** Classified star catalog and background-subtracted luminance image from A1.

### Steps

**A2.1 — Star profile fitting.** For each detected star (or a spatially representative random sample if thousands are detected — maintain field coverage), cut out a small subimage (e.g., 21x21 pixels) centered on the star position. **Saturation check before fitting:** a star is saturated if `z_peak >= 0.99` AND `np.sum(z >= z_peak * 0.999) > 3` (i.e., a plateau of clipped adjacent pixels, not just a bright peak). Skip saturated stars from profile fitting as their shapes are unreliable; count and report the saturation fraction separately. Fit a 2D Moffat profile to non-saturated stars using `photutils` PSF fitting or `scipy.optimize.curve_fit`. Moffat is preferred over Gaussian because it better models the wings of real optical PSFs. Extract per-star:
- FWHM (focus/sharpness)
- Eccentricity (tracking accuracy — round stars = good tracking)
- Position angle of elongation
- Moffat beta parameter (wing steepness)
- Fit residual / chi-squared (model quality)

**A2.2 — Field variation analysis.** Aggregate per-star measurements:
- Median FWHM and spatial variation — consistent across the frame = good optical alignment; increasing toward corners = tilt or field curvature. Compute `corner_excess_fwhm` = (median FWHM in corner quadrants) / (median FWHM in center quadrant) as a tilt/curvature indicator.
- Eccentricity pattern classification using the following algorithm:
  1. For each star, compute a double-angle unit vector weighted by eccentricity: `(ecc * cos(2*PA), ecc * sin(2*PA))`. Round stars (low eccentricity) contribute negligibly.
  2. Compute the mean resultant length **R** of these vectors. R near 1.0 = all elongations point the same direction; R near 0.0 = random.
  3. Compute a radial alignment score: for each star, compute the angle from the image center to the star's position (`radial_pa`), then `radial_score = mean(cos(2 * (elongation_pa - radial_pa)))`. Score near 1.0 = elongations point radially outward (coma pattern).
  4. Decision tree: median eccentricity < 0.15 → `clean`; R > 0.6 → `uniform_drift` (tracking error); radial_score > 0.4 → `radial` (optical aberration/coma); else → `random_seeing`.
- Flag outliers that are likely misclassified extended objects.

**A2.3 — Star color extraction.** For color images, extract RGB channel ratios for bright unsaturated stars from their cutout subimages. Store for use in A6.

### Output
- Per-star measurements table (with saturation flags)
- Field summary statistics (median FWHM, FWHM spatial variation, corner_excess_fwhm, median eccentricity, eccentricity pattern classification with R and radial_score values, saturation fraction)
- Star color ratio table (for A6)
- Diagnostic visualization: star map color-coded by FWHM with eccentricity vectors

---

## A3 — Noise Characterization

**Dependencies:** `numpy` (`numpy.fft` for PSD computation, statistics), `scipy` (`scipy.signal.welch` for PSD estimation, `scipy.stats` for distribution fitting and normality tests like `normaltest`/`shapiro`, `scipy.ndimage` for autocorrelation)

**Input:** Segmentation mask and RMS map from A1; background-subtracted image.

### Steps

**A3.1 — Background patch statistics.** Using the segmentation mask, isolate background-only regions. Compute per-channel: mean, standard deviation (noise floor), skewness, kurtosis. Skewness and kurtosis reveal whether the distribution is Gaussian (well-behaved shot noise) or has heavy tails (calibration problems, signal contamination). Use `scipy.stats.normaltest` or `shapiro` for formal normality testing.

**A3.2 — Power spectral density.** Compute PSD of multiple background patches via `scipy.signal.welch` or direct FFT and averaging. Characterize the spectral shape:
- Flat PSD = white noise (well-calibrated, minimal processing) — this is the ideal.
- Suppressed mid/high frequencies = aggressive noise reduction (waxy, plastic look).
- Boosted high frequencies = oversharpening.
- Quantify as spectral slope and/or ratio of power in frequency bands (low/mid/high).
- **Note:** `psd_high_band_suppression` is biased by image domain — JPEG DCT artifacts add high-frequency energy that inflates this metric in display-domain images. See Reference Set Caveat.

**A3.3 — Noise autocorrelation.** Compute autocorrelation of background patches via `scipy.ndimage` or direct computation. In well-behaved noise, autocorrelation drops to zero at one-pixel lag. Wider autocorrelation peak = correlated noise from drizzling, resampling, or noise reduction. The correlation width is a direct measure of processing-induced smearing. **Note:** `autocorr_width_px` is biased by JPEG 8x8 block structure in display-domain images — the block boundaries introduce periodic correlation that inflates this metric independent of processing quality.

**A3.4 — SNR estimation.** In target regions from the segmentation mask, compute signal level relative to local noise from the RMS map.

### Output
- Noise floor per channel (standard deviation)
- SNR in target regions
- PSD shape parameters (spectral slope, band power ratios)
- Noise autocorrelation width
- Distribution statistics (skewness, kurtosis, normality test p-values)

---

## A4 — Frequency Domain Analysis of Target Structure

**Dependencies:** `numpy` (`numpy.fft` for 2D FFT, array operations), `scipy` (`scipy.signal` for windowing functions like Hann/Tukey, `scipy.optimize` for power law fitting)

**Input:** Segmentation mask from A1, FWHM measurements from A2, background-subtracted image.

### Steps

**A4.1 — Region extraction and windowing.** Cut out regions containing target signal using the segmentation mask. Apply a window function (`scipy.signal.windows.tukey` or `hann`) to avoid spectral leakage from sharp region edges.

**A4.2 — Power spectrum computation.** Compute 2D FFT, then convert to radial power spectrum by azimuthally averaging (bin by radial frequency, average power in each bin).

**A4.3 — Spectral slope fitting.** Real astronomical detail follows a power law falloff. Fit the slope using `scipy.optimize.curve_fit`. The frequency where the power spectrum flattens into the noise floor defines the effective resolution of the image.

**A4.4 — Artifact detection.**
- Oversharpening: bump or plateau at specific frequencies where a sharpening kernel boosted power above the natural power law falloff.
- Ringing: periodic features in the power spectrum.
- PSF consistency check: compare target power spectrum against the PSF-defined cutoff from A2's FWHM — target detail shouldn't contain significant power above what the optical PSF allows. If it does, those are processing artifacts.

### Output
- Radial power spectrum (array)
- Fitted spectral slope
- Effective resolution estimate
- Artifact detection flags with frequency locations

---

## A5 — Gradient and Calibration Assessment

**Dependencies:** `numpy` (`numpy.polynomial` for polynomial surface fitting, array statistics), `scipy` (`scipy.optimize` for radial vignetting model fitting)

**Input:** Background map from A1, RMS map from A1, **segmentation mask from A1**.

### Steps

**A5.1 — Gradient measurement.** Fit a low-order polynomial surface (2nd or 3rd order via `numpy.polynomial.polynomial.polyvander2d` or similar) to the background map. In a well-processed image this should be essentially flat. Quantify gradient magnitude as peak-to-peak variation of the fitted surface relative to the background noise level from the RMS map. Rule of thumb: gradient 3x noise = noticeable; 10x = serious problem. Record gradient direction. **Note:** `gradient_ratio` is expressed in noise-floor units, which makes it sensitive to image domain — JPEG quantization alters the noise floor. **Implementation:** the polynomial is fit on a stride-downsampled background (~128 px per side, matching A5.2). Six coefficients are massively overdetermined by 16K+ samples; full-resolution fitting would allocate ~1.2 GB just for the design matrix on a 24 MP image and OOM a 4 GB pipeline machine.

**A5.2 — Vignetting detection.** Fit a radial falloff model from the image center using `scipy.optimize`. **Critical: fit on background-mask pixels only** (use the segmentation mask from A1 to exclude target-signal pixels), or at minimum weight down target-mask pixels heavily. Without this masking, bright central targets (e.g., emission nebulae centered in the frame) are misread as vignetting falloff — the radial fit sees a bright center and dim corners and interprets it as optical vignetting rather than the target itself. Quantify as percentage falloff from center to corners.

**A5.3 — Per-channel background color balance.** Compute background level independently for R, G, B channels. If they don't match, there's a color cast indicating incomplete color calibration. Measure deviation from neutral gray as a vector in color space.

### Output
- Gradient magnitude and direction
- Vignetting residual (center-to-corner percentage falloff)
- Per-channel background levels
- Color balance deviation from neutral

---

## A6 — Color Analysis

**Dependencies:** `scikit-image` (color space conversions), `numpy` (histogram computation, statistics), `scipy` (`scipy.spatial.distance` for color distribution comparison)

**Input:** Star color extractions from A2, segmentation mask from A1, per-channel background levels from A5, color channel arrays from A1.

### Steps

**A6.1 — Star color accuracy.** Using RGB ratios extracted in A2, compare against known stellar color indices (B-V photometric references). Well-calibrated images preserve realistic star colors — blue-white hot stars through yellow-orange cool stars. If all stars look the same color, color information was destroyed in processing. Quantify as correlation between measured star color ratios and expected color index values. Also compute `star_color_diversity` — the spread (standard deviation) of star color ratios across the detected star population. Low diversity suggests color information was crushed.

**A6.2 — Background neutrality.** Extend the per-channel analysis from A5 into full color space — compute chromaticity of background regions and measure distance from neutral (`bg_chroma_distance`). Uses segmentation mask to isolate background. **Note:** color metrics are biased by JPEG chroma subsampling in display-domain images.

**A6.3 — Target color distribution.** For broadband RGB: check for realistic emission colors (hydrogen-alpha red, OIII blue-green). For narrowband palette-mapped images: detect automatically via color histogram signature — SHO/Hubble palette has distinctive channel separations. If narrowband palette is detected, flag that color evaluation requires different reference standards since palette mapping is partly artistic.

### Output
- Star color accuracy score
- Star color diversity score
- Background neutrality measure (bg_chroma_distance)
- Target color distribution analysis
- Narrowband palette detection flag
- Overall color calibration assessment

---

## A7 — Scoring and Comparison

**Dependencies:** `numpy` (percentile calculations, statistical comparisons), `scipy` (`scipy.stats` for distribution fitting and percentile ranking), `matplotlib` (radar charts, scorecards, diagnostic plots)

**Input:** All metric outputs from A2–A6; image domain tag from A1; cached APOD reference distributions from A8.

### Steps

**A7.1 — Reference matching.** Match the submitted image to the appropriate APOD reference set using an explicit fallback chain:

1. **Same target** (preferred): match by specific object name (e.g., `rosette_nebula`, `orion_nebula`, `horsehead_nebula`, `north_america_nebula`). Same-target comparisons are the most meaningful even with small n — comparing two Rosettes is more informative than comparing a Rosette against a grab bag of emission nebulae. Per-target sub-categories are first-class entries in the reference distributions, siblings of broader categories.
2. **Same category**: if no same-target match exists or n is too small (< 3), fall back to the broader category (e.g., `emission_nebula`, `galaxy`, `planetary_nebula`, `star_cluster`, `widefield`).
3. **Global pool**: if same-category n is also too small (< 5), fall back to the global pool — all APOD images regardless of category. The global pool is always present as a first-class reference set (see A8.4), not an afterthought.

Report which reference tier was used on the scorecard so the user knows the basis of comparison.

**A7.2 — Percentile scoring.** For each metric, compute where the submitted image falls in the matched reference distribution. Use `scipy.stats` for distribution fitting if reference distributions aren't well-approximated by simple parametric forms. Essentially a z-score or percentile rank on each dimension.

**A7.3 — Scorecard generation.** Produce a radar chart with 5 composite axes. Each axis aggregates multiple metrics with the following weights:

- **Star quality** = `median_fwhm` (1.0) + `median_eccentricity` (1.0) + `corner_excess_fwhm` (0.5)
- **Noise management** = `noise_floor` (0.5) + `psd_high_band_suppression` (1.0) + `autocorr_width_px` (1.0) + `snr` (0.5)
- **Detail resolution** = `target_spectral_slope` (1.0) + `effective_resolution` (1.0) + `bg_psd_slope` (0.5)
- **Gradient control** = `gradient_ratio` (1.0) + `vignetting_falloff` (1.0)
- **Color calibration** = `color_overall` (1.0) + `star_color_diversity` (1.0) + `bg_chroma_distance` (0.5) + `color_balance_deviation` (0.5)

Within each axis, the composite score is the weighted average of the per-metric percentile scores. These weights are initial values informed by implementation experience — they should be tuned as the reference archive grows and real user feedback accumulates.

**A7.4 — Diagnostic text.** Generate specific actionable feedback based on which metrics are weak and what patterns they form. Examples:
- "Star eccentricity is elevated with a consistent NW-SE orientation across the field, suggesting periodic error in RA tracking. FWHM is excellent, so focus and optics are not the issue."
- "Background PSD shows suppressed mid-frequencies consistent with aggressive luminance noise reduction. Consider lighter noise reduction to preserve natural noise texture."
- "SNR is strong but the power spectrum shows a sharpening artifact bump at 0.3 cycles/pixel. The underlying detail is good — less aggressive sharpening would score better."

**A7.5 — Domain mismatch warning.** If the submitted image's domain tag (from A1.7) is `linear` and the reference set is display-domain (which it always is for APOD JPEGs), emit a prominent warning on the scorecard: "This image is linear/high-bit-depth data being compared against display-format JPEG references. Metrics affected by format artifacts (autocorrelation width, PSD high-band suppression, gradient ratio, color metrics) may show misleadingly favorable scores. For the most meaningful comparison, evaluate the final processed display-format output rather than the linear master."

### Output
- Per-metric percentile scores
- Composite axis scores with weights
- Reference tier used (same_target / same_category / global)
- Radar chart visualization
- Diagnostic text report
- Domain mismatch warning (if applicable)
- Raw metric values for downstream analysis or export

---

## A8 — Reference Archive Pipeline

**Dependencies:** `multiprocessing` or `joblib` (parallelization), `json` (serialization), all dependencies from A1–A6

**Input:** APOD images and metadata from A0.

The same analysis pipeline (A1–A6) must be run against the APOD archive to build the reference distributions that A7 uses for scoring. This is a one-time batch job (~10,000 images) with incremental daily updates.

### Steps

**A8.1 — Batch processing.** Run A1–A6 against all downloaded APOD images. Ensure `sep.set_extract_pixstack(5_000_000)` is called at startup. Full reprocessing of 10,000 images at ~30 seconds each = ~83 hours sequential. Parallelize across cores with `multiprocessing` or `joblib` to bring this to ~10-15 hours on 8 cores. Run overnight once, then one image per day going forward.

**A8.2 — Per-image summary caching.** Cache a JSON summary of the final metrics for each processed APOD image (one JSON file per image containing all stage outputs as scalars/small arrays). This is whole-summary-per-image caching, not true per-stage caching. The spec originally called for independent per-stage caching (so changing A2's star fitting wouldn't require rerunning A1), but in practice this is non-trivial: stages A1 and A2 share large numpy arrays (background-subtracted image, source catalog) and serializing those per-stage adds significant disk and I/O cost. The simpler trade-off: to invalidate one stage, delete the image's summary and re-run the full pipeline for that image. **Future extension:** if iteration on A2/A3 becomes the bottleneck, implement true stage-level caching with serialized intermediate arrays.

**A8.3 — Target categorization.** Extract or infer target categories from APOD titles and explanation text. **Title is strongly preferred over explanation** — APOD explanations are dense paragraphs that mention many objects in passing, leading to mis-categorization (e.g., "Star Cluster IC 348" tagged as `solar_system` because the explanation mentions the Moon; "Northern Lights" tagged as `galaxy` because the explanation mentions a galaxy in the background).

Categorization rules must be applied in a specific priority order:

1. **Per-target matching (highest priority).** Check title for specific named targets: `rosette_nebula`, `orion_nebula`, `horsehead_nebula`, `north_america_nebula`, `andromeda_galaxy`, `whirlpool_galaxy`, etc. These become sub-category entries that are checked before broader categories.
2. **Atmospheric/solar system disqualifiers.** Check for aurora, meteor, comet, eclipse, Moon, Sun, planet names. A "comet near a galaxy" landscape must tag as `solar_system`/`comet`, not `galaxy`. Look for `widefield_landscape` patterns: "X over Y" / "X rises above Y" / "X and Y over Z" in titles — these are typically landscape astrophotos, not deep-sky images.
3. **Deep-sky categories.** Emission nebula, planetary nebula, reflection nebula, galaxy, star cluster, supernova remnant, etc.
4. **Uncategorized bucket.** Many APODs won't match any rule cleanly — illustrations, diagrams, composites, artistic renderings. An `uncategorized` category is normal and expected. The global pool (A8.4) serves as the fallback reference for these.

**A8.4 — Distribution building.** Aggregate per-metric results across all APOD images within each category (including per-target sub-categories) to build the reference distributions that A7 scores against.

**Distribution format:** A JSON file containing a dict keyed by category name. Each category entry is a dict keyed by metric name. Each metric entry contains:
```json
{
  "metric": "median_fwhm",
  "higher_is_better": false,
  "n": 47,
  "values": [2.1, 2.3, 2.5, ...],
  "quantiles": {
    "p10": 1.8,
    "p25": 2.1,
    "p50": 2.5,
    "p75": 3.2,
    "p90": 4.1
  }
}
```

The `"global"` pool is always present as a top-level category entry containing all APOD images regardless of category. Per-target sub-categories (e.g., `rosette_nebula`) are sibling entries to broader categories (e.g., `emission_nebula`), not nested.

**Future extension:** format-stratified distributions (separate distributions for JPEG vs. PNG vs. TIFF APOD images) to reduce format-domain bias in scoring. Currently all APOD images are pooled regardless of format since the archive is overwhelmingly JPEG.

---

## A9 — MCP Server and LLM Conversation Layer

**Dependencies:** `mcp` (Model Context Protocol SDK), `json`, all dependencies from A1–A7

**Architecture:** An MCP server that wraps the measurement pipeline (A0–A8) and exposes it as tools. Any MCP-compatible client — Claude Desktop, Claude.ai with connectors, a custom chat UI — connects to the server and gets access to grounded astrophotography evaluation. The LLM handles the natural language conversation natively; the MCP server just provides the data. **The LLM never sees the raw image — only structured metrics returned by tool calls.**

This means A9 is not an LLM integration in the codebase. It's a tool server. The conversation quality comes from the LLM client, and the evaluation quality comes from the deterministic pipeline. Clean separation.

### Steps

**A9.1 — MCP server scaffold.** Build the MCP server using the MCP SDK. Register tools, handle connections, manage the lifecycle of evaluation requests. The server should be runnable locally (`stdio` transport for Claude Desktop / Claude Code) or remotely (`SSE` transport for web clients).

**A9.2 — Tool: `evaluate_image`.** Accepts an image path, runs A1–A6, returns the full structured metrics as JSON. This is the heavyweight call — the user submits an image and gets back every measurement. The tool description should clearly explain what metrics are returned so the LLM knows how to interpret the results.

**A9.3 — Tool: `score_image`.** Accepts an image path and optional target type (e.g., "emission nebula", "galaxy"). Runs A1–A7, returns the percentile scorecard — where the image ranks on each quality dimension relative to the APOD reference set. If target type isn't specified, attempts auto-detection or asks the user via the LLM.

**A9.4 — Tool: `get_stage_detail`.** Accepts an image path and a stage identifier (e.g., "star_field", "noise", "color"). Returns a deep dive on just that stage's results. Useful for conversational follow-up — the user asks "tell me more about my star shapes" and the LLM calls this tool for the detailed A2 output without rerunning the full pipeline.

**A9.5 — Tool: `list_reference_matches`.** Accepts an image path or target type. Returns the APOD images being used as the comparison set — titles, dates, URLs, and their metric values. Lets the LLM show the user exactly what they're being measured against.

**A9.6 — Tool: `get_submission_history`.** Accepts a user identifier. Returns prior scorecards for longitudinal tracking. Enables the LLM to identify trends across submissions: "Your noise management has improved over your last three submissions, but your gradient control has gotten worse — did you change your flat fielding process?"

**A9.7 — Tool: `get_diagnostic_context`.** Accepts a metric name and a score. Returns domain-specific context about what that metric means, what common causes of poor scores are, and what the user can do about them. This is a lightweight reference tool — essentially a structured knowledge base about astrophotography quality factors that the LLM can query to give better advice. Keeps the domain knowledge in the server rather than relying on the LLM's training data. **Must include a `_reference_caveat` special entry** that explains what the reference set is (APOD display JPEGs), why it's biased (format artifacts baked into the distributions), and how to mitigate (evaluate display-format outputs, not linear masters). The LLM should be able to surface this context when explaining any score to the user.

**A9.8 — Caching and session management.** Cache evaluation results per image so that follow-up tool calls (get_stage_detail, list_reference_matches) don't rerun the pipeline. Manage sessions so longitudinal tracking works across conversations.

### Output
- MCP server exposing the measurement pipeline as tools
- Any MCP-compatible LLM client can connect and provide the natural language conversation layer
- No LLM code in the apodornot codebase — the server is pure tools and data

---

## Implementation Notes

- **Start with A0, A1, and A2.** Get the API client working, then image loading, background estimation, source detection, and star profile fitting end to end on a real astrophoto before building later stages. A2 is the most immediately diagnostic and exercises the core infrastructure.
- **Design each stage as an independent module** with a clear input/output contract. Top-level orchestrator composes them.
- **Call `sep.set_extract_pixstack(5_000_000)` at pipeline startup** before any SEP operations. This prevents pixstack overflows on nebula-rich frames.
- **Profile on real full-resolution astro images** before optimizing. Most of the heavy computation happens in compiled C/Fortran code under numpy/scipy/SEP — Python overhead is minimal. If specific bottlenecks appear, use `numba` JIT on those inner loops.
- **Test images:** Use a mix of known-good APOD images and typical amateur images at various quality levels to validate that the metrics actually discriminate quality. Include both display-format (JPEG) and linear (FITS) test images to verify domain mismatch detection.
- **Expect an `uncategorized` bucket.** Many APOD entries won't match categorization rules. This is normal — the global pool serves as the fallback reference.
