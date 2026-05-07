# Changelog

What's changed in apodornot, in reverse-chronological order. Edit this file
and Phoenix recompiles — `ApodornotWeb.Changelog` parses it at compile time.

Only user-facing changes belong here. Plumbing, infrastructure, and bug
fixes invisible to users go in commit messages, not here.

---

## 2026-05-06

### Scoring

- **Effective resolution removed from scoring.** The metric was clustered tightly across the entire APOD reference set — every image got nearly the same value on it — so it was a constant offset that contributed nothing to discrimination. The Detail-resolution axis is now driven by spectral slope alone. The chart still shows an `eff res` marker for visual orientation, but it no longer affects your overall score.

### Charts

- **Radial-PSD chart got readable axis labels.** Decade ticks (0.01, 0.02, 0.05, 0.1, 0.2, 0.5) along the bottom so the marker is interpretable in actual cy/px. The "eff res = N cy/px" label now anchors to whichever side of the line has more room, so it stops clipping past the edge.

## 2026-05-05

### Live deployment

- **Live at apodornot.com.** Custom domain with TLS. The `apodornot-web.fly.dev` URL still works as a fallback.
- **Sign-in required.** Invite-only access via a shared passcode. Daily chat-turn cap to keep the LLM bill predictable.

### User Experience

- **Image preview survives across sessions.** Reloading an old submission now reliably shows the image you uploaded, not a broken-image icon.
- **`target` selection is visible on the scorecard.** Under the overall score: `target: rosette · your pick` (or `· auto from filename`) so you can confirm the system used what you picked.
- **Recent submissions dropdown** in the top-left. Shows your last 10 submissions across sessions; click to revisit.
- **Cold-start spinner on the score page.** When the pipeline has been idle, the first request shows a "waking the pipeline" spinner instead of a blank loading state.
- **Submit shows a progress bar.** A 50 MB upload is no longer an opaque wait between picking a file and seeing A1 fire.
- **Cursor pointer on axis cards.** They were always clickable — now they look it.
- **Glossary terms in chat are clickable.** Every "FWHM", "PSD slope", "tracking error", etc. opens an explanation panel with beginner / intermediate / advanced levels. The panel itself is also linkified — click "ADU" inside the SNR explanation and the panel swaps to ADU.
