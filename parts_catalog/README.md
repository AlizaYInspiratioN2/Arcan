# Parts Catalog Lookup (starter MVP)

A local-first API and browser UI for vehicle selection, part-number/product search, fitment records, and authorized product-image storage. It does **not** ship an OEM catalog or scrape third-party sites. Populate it only with data you own or are authorized to use; record data and image sources/licenses.

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

## Import real data from a local CSV

The importer accepts normalized CSV files from an authorized export, supplier feed, or data you have permission to use. It reads local files only: it does not fetch URLs, crawl websites, or bypass access restrictions. A page appearing in Google search does not by itself grant permission to reuse its catalog data or images. Use a provider's documented export/feed and follow its terms. The importer does not connect to 7zap; only use a 7zap export if you have permission to obtain and reuse it.

Required CSV headers are `part_number`, `title`, and `part_source`. Optional columns:

```text
brand,description,category,make,model,year_start,year_end,engine,engine_code,region,vehicle_source,fitment_notes,fitment_source,image_path,image_alt_text,image_source,image_license
```

Each row describes a part and optionally one vehicle fitment. Repeat a part row for its other vehicle applications. Leave all vehicle columns blank for a part without fitment data. Image paths are local paths relative to `--images-dir`; remote image URLs are not downloaded. If supplying an image, include its source and license/permission details. JPEG, PNG, and WebP files up to 5 MiB are accepted.

Run a dry-run first (this is the default):

```bash
python -m parts_catalog.importer /path/to/authorized-catalog.csv --images-dir /path/to/authorized-images
```

Apply valid rows to the local database after reviewing the report:

```bash
python -m parts_catalog.importer /path/to/authorized-catalog.csv --images-dir /path/to/authorized-images --apply
```

The importer is idempotent for matching part/vehicle/fitment/image records, updates non-empty fields for an existing part number, processes database writes in batches, and reports invalid rows by CSV line number. Keep backups before importing updates. For large production imports, use PostgreSQL and test on a staging database first.

## Add records through the API

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

## Lookup API

- `GET /api/vehicles?make=Volkswagen&model=Golf&year=2018` — select/filter vehicle applications.
- `GET /api/parts?q=EXAMPLE-001` — search part number, brand, or title.
- `GET /api/parts?q=brake&vehicle_id=1` — restrict part search to recorded vehicle fitments.
- `GET /api/parts/{id}` — part, image URLs, and fitments.
- `GET /media/{filename}` — serve stored images.

Each vehicle entry represents a year range and optional engine/application details. Fitment may depend on exact engine, market, production date, or PR codes; store caveats in fitment notes and do not claim broader compatibility than your source supports.

## Production notes

This is a starter application, not a complete OEM catalog or production-hardened 100,000-part service. Before deploying publicly, add authentication for reads, PostgreSQL and migrations, backups, import job monitoring, tests, and object storage if needed. Connect a future provider through its documented, authorized feed/export and preserve provider IDs, source, license/permission, and sync timestamps.
