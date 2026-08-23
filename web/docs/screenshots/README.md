# Screenshots

Images referenced by the root README. The README's screenshot block is
commented out until these exist — uncomment it once you add them.

Expected files: `upload.png`, `scorecard.png`.

## Capturing

Both the pipeline service and this app need to be running:

```bash
# in the apodornot checkout
apodornot-web --port 8000

# here
docker compose up -d
mix phx.server        # http://localhost:4000
```

Upload an image at `/` for `upload.png` (catch it mid-run, while stages are
still streaming in), then let it finish and capture the scorecard at
`/s/<submission_id>`.
