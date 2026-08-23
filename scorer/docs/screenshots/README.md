# Screenshots

Images referenced by the root README. The README's screenshot block is
commented out until these exist — uncomment it once you add them.

Expected files: `scorecard.png`, `stage-detail.png`.

## Capturing

The CLI can render a radar plot directly, with no web frontend involved:

```bash
apodornot fetch --start 2024-01-15 --end 2024-01-15 --output apod_archive
apodornot score apod_archive/2024/2024-01-15.jpg \
    --target-type emission_nebula --radar docs/screenshots/scorecard.png
```

For the full scorecard UI, run the pipeline service and the LiveView frontend
from the apodornot-web repository:

```bash
apodornot-web --port 8000
```
