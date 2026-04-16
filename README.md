# Rhiza Bar Reader Service

A tiny Vercel Python serverless function that reads the colored zone bars on DSL GI MAP patient PDFs. Given a PDF, it returns the zone (red/yellow/green) that each marker's triangle sits in, plus a flag status (HIGH / LOW / elevated / lower_than_ideal / normal).

This lets the Rhiza engine match Michelle's clinical reading of the report — she calls yellow-zone values "elevated" even when they're within the lab reference range, and the bar reader just sees the same zone she does.

## API

**POST /api/analyze**

Body: multipart/form-data with a `pdf` file, OR raw PDF bytes as the body.

Response:
```json
{
  "success": true,
  "markers": [
    {
      "page": 3,
      "marker": "Bacteroides fragilis",
      "zones": [{"zone": "R", "start": 0, "end": 12}, ...],
      "triangle_pct": 3.1,
      "triangle_zone": "R",
      "flag": "LOW"
    }
  ]
}
```

## Deployment

Deploy as its own Vercel project:

```bash
cd bar-reader-service
vercel
```

Follow the prompts. Once deployed, Vercel gives you a URL like `https://rhiza-bar-reader.vercel.app`. Set that URL in the main Rhiza Next.js app as an env var: `BAR_READER_URL`.

## Local testing

```bash
pip install -r requirements.txt
python -m http.server  # rough dev server — or use vercel dev
```

## Exceptions list

Michelle's rule: for Enterobacter spp., K. pneumoniae, and E. faecalis, only flag extremes (HIGH or LOW) — ignore the yellow zone. This is hardcoded in `analyze.py` in the `EXTREMES_ONLY` set.
