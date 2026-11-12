# Parts Catalog Lookup (starter MVP)

A small local-first API and browser UI for vehicle selection, part-number/product search, fitment records, and authorized product-image storage. It intentionally does **not** scrape third-party sites or ship an OEM catalog. Populate it only with data you own or are authorized to use; record data and image sources/licenses.

## Run locally

Requires Python 3.10+.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
source .venv/bin/activate
pip install -r parts_catalog/requirements.txt
export CATALOG_ADMIN_TOKEN='replace-with-a-long-random-secret'
uvicorn parts_catalog.main:app --reload
```

Open <http://127.0.0.1:8000>. Interactive API documentation is at <http://127.0.0.1:8000/docs>. The SQLite database and uploaded images are created in `parts_catalog/data/`. Set `CATALOG_DATA_DIR` to move them outside the repository. Do not commit catalog data, uploaded images, or production secrets.

## Add authorized catalog records

All write endpoints require `X-Admin-Token` matching `CATALOG_ADMIN_TOKEN`. Read/search endpoints are public by default; put authentication in front of them before deploying private catalog data.

Create vehicle application:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/vehicles \\
  -H 'Content-Type: application/json' -H 'X-Admin-Token: replace-with-a-long-random-secret' \\
  -d '{"make":"Volkswagen","model":"Golf VII","year_start":2012,"year_end":2020,"engine":"1.4 TSI","region":"EU","source":"authorized-data-source"}'
```

Create a part and fitment (use the returned IDs):

```bash
curl -X POST http://127.0.0.1:8000/api/admin/parts \\
  -H 'Content-Type: application/json' -H 'X-Admin-Token: replace-with-a-long-random-secret' \\
  -d '{"part_number":"EXAMPLE-001","brand":"Example","title":"Front brake pad set","category":"Brakes","source":"authorized-data-source"}'

curl -X POST http://127.0.0.1:8000/api/admin/fitments \\
  -H 'Content-Type: application/json' -H 'X-Admin-Token: replace-with-a-long-random-secret' \\
  -d '{"part_id":1,"vehicle_id":1,"notes":"Verify production date and PR codes","source":"authorized-data-source"}'
```

Upload an image only when you have permission to store and display it. JPEG, PNG, and WebP are accepted up to 5 MiB; image source and license metadata can be recorded:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/parts/1/images \\
  -H 'X-Admin-Token: replace-with-a-long-random-secret' \\
  -F 'file=@part-photo.jpg' -F 'alt_text=Front brake pad set' \\
  -F 'source=Supplier name' -F 'license=Written permission, 2026-01-01'
```

## Lookup API

- `GET /api/vehicles?make=Volkswagen&model=Golf&year=2018` — select/filter vehicle applications.
- `GET /api/parts?q=EXAMPLE-001` — search part number, brand, or title.
- `GET /api/parts?q=brake&vehicle_id=1` — restrict part search to recorded vehicle fitments.
- `GET /api/parts/{id}` — part, image URLs, and fitments.
- `GET /media/{filename}` — serve stored images.

Each vehicle entry represents a year range and optional engine/application details. Fitment data may need exact engine, market, production-date, or PR-code qualification; store those caveats in fitment notes and do not imply broader compatibility than the source supports.

## Connecting a future authorized feed

Keep the feed integration separate from lookup routes: map the provider's documented/exported format into the vehicle, part, fitment, and image records above; preserve provider IDs, source, license/permission, and sync timestamps in a future importer. Use the provider's published limits and terms. This starter is suitable for a small prototype, not yet a production 100,000-part deployment; add PostgreSQL, migrations, background batch imports, backups, observability, and access controls before scaling or exposing it publicly.
