# Screenshots

Images used by the root README: `upload.png`, `scorecard.jpg`,
`axis-detail.jpg`, `chat.jpg`.

Captured at 3456px wide and downscaled to 1600px. The three containing the
astrophoto are JPEG — as PNG they came to 2.3 MB between them, against 750 KB
at quality 92 with no visible difference at this size.

## Capturing

Both halves have to be running:

```bash
# scorer
cd scorer && source .venv/bin/activate && apodornot-web --port 8000

# web
cd web && docker compose up -d && mix phx.server   # http://localhost:4000
```

The chat panel needs `ANTHROPIC_API_KEY`; everything else works without it.
See `.env.example`.

| File | What it shows |
|---|---|
| `upload.png` | `/` with an image staged, target type and context controls |
| `scorecard.jpg` | `/s/<id>` — image, overall score, five axis cards, radar |
| `axis-detail.jpg` | An axis drawer open, showing its diagnostic plot |
| `chat.jpg` | The chat panel answering against the scorecard |

An image with real weaknesses makes a better screenshot than a flawless one —
the findings and the chat have something to say.
