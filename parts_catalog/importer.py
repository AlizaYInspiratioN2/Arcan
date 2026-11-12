"""Import authorized parts data from a normalized CSV file.

This importer reads local files only. It never fetches URLs or crawls websites.
Run from the repository root with: python -m parts_catalog.importer --help
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from parts_catalog.main import (
    ALLOWED_IMAGES,
    IMAGE_DIR,
    Part,
    PartImage,
    SessionLocal,
    Vehicle,
    Fitment,
)

REQUIRED_COLUMNS = {"part_number", "title", "part_source"}
VEHICLE_COLUMNS = {"make", "model", "year_start", "year_end"}
TEXT_LIMITS = {
    "part_number": 100, "brand": 100, "title": 240, "category": 100,
    "part_source": 500, "make": 80, "model": 100, "engine": 120,
    "engine_code": 80, "region": 80, "vehicle_source": 500,
    "fitment_source": 500, "fitment_notes": 1000, "image_alt_text": 500,
    "image_source": 500, "image_license": 1000,
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


@dataclass
class ImportReport:
    rows_read: int = 0
    rows_imported: int = 0
    parts_created: int = 0
    parts_updated: int = 0
    vehicles_created: int = 0
    fitments_created: int = 0
    images_added: int = 0
    rows_skipped: int = 0
    errors: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def clean(row: dict[str, str | None], name: str) -> str | None:
    value = row.get(name)
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if name in TEXT_LIMITS and len(value) > TEXT_LIMITS[name]:
        raise ValueError(f"{name} exceeds {TEXT_LIMITS[name]} characters")
    return value


def parse_year(value: str | None, field: str) -> int:
    if value is None:
        raise ValueError(f"{field} is required when vehicle fitment is supplied")
    try:
        year = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a whole number") from exc
    if not 1886 <= year <= 2200:
        raise ValueError(f"{field} must be between 1886 and 2200")
    return year


def read_image(image_root: Path, relative_name: str) -> tuple[bytes, str, str]:
    root = image_root.resolve()
    path = (root / relative_name).resolve()
    if path == root or root not in path.parents:
        raise ValueError("image_path must stay inside the supplied --images-dir")
    if not path.is_file():
        raise ValueError(f"image file not found: {relative_name}")
    content = path.read_bytes()
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("image must be non-empty and no larger than 5 MiB")
    detected: tuple[str, str] | None = None
    for mime, (extension, signature) in ALLOWED_IMAGES.items():
        if content.startswith(signature) and (mime != "image/webp" or content[8:12] == b"WEBP"):
            detected = (mime, extension)
            break
    if detected is None:
        raise ValueError("only valid JPEG, PNG, or WebP images are accepted")
    return content, detected[0], detected[1]


def process_row(row: dict[str, str | None], db, image_root: Path, report: ImportReport) -> None:
    part_number = clean(row, "part_number")
    title = clean(row, "title")
    source = clean(row, "part_source")
    if not part_number or not title or not source:
        raise ValueError("part_number, title, and part_source are required")

    part = db.scalar(select(Part).where(Part.part_number == part_number))
    values = {
        "brand": clean(row, "brand"), "title": title,
        "description": clean(row, "description"),
        "category": clean(row, "category"), "source": source,
    }
    if part is None:
        part = Part(part_number=part_number, **values)
        db.add(part)
        db.flush()
        report.parts_created += 1
    else:
        changed = False
        for key, value in values.items():
            if value is not None and getattr(part, key) != value:
                setattr(part, key, value)
                changed = True
        if changed:
            report.parts_updated += 1
        db.flush()

    vehicle_values = {name: clean(row, name) for name in VEHICLE_COLUMNS - {"year_start", "year_end"}}
    start_raw, end_raw = clean(row, "year_start"), clean(row, "year_end")
    has_vehicle_data = any(vehicle_values.values()) or start_raw is not None or end_raw is not None
    if has_vehicle_data:
        if not all(vehicle_values.get(key) for key in ("make", "model")):
            raise ValueError("make and model are required for a fitment")
        year_start = parse_year(start_raw, "year_start")
        year_end = parse_year(end_raw, "year_end")
        if year_end < year_start:
            raise ValueError("year_end must be greater than or equal to year_start")
        vehicle_query = select(Vehicle).where(
            Vehicle.make == vehicle_values["make"], Vehicle.model == vehicle_values["model"],
            Vehicle.year_start == year_start, Vehicle.year_end == year_end,
            Vehicle.engine == clean(row, "engine"),
        )
        vehicle = db.scalar(vehicle_query)
        if vehicle is None:
            vehicle = Vehicle(
                make=vehicle_values["make"], model=vehicle_values["model"],
                year_start=year_start, year_end=year_end,
                engine=clean(row, "engine"), engine_code=clean(row, "engine_code"),
                region=clean(row, "region"), source=clean(row, "vehicle_source") or source,
            )
            db.add(vehicle)
            db.flush()
            report.vehicles_created += 1
        notes = clean(row, "fitment_notes")
        fitment_stmt = select(Fitment).where(
            Fitment.part_id == part.id, Fitment.vehicle_id == vehicle.id,
            Fitment.notes.is_(None) if notes is None else Fitment.notes == notes,
        )
        fitment = db.scalar(fitment_stmt)
        if fitment is None:
            db.add(Fitment(
                part_id=part.id, vehicle_id=vehicle.id, notes=notes,
                source=clean(row, "fitment_source") or source,
            ))
            db.flush()
            report.fitments_created += 1

    image_path = clean(row, "image_path")
    if image_path:
        image_source = clean(row, "image_source")
        image_license = clean(row, "image_license")
        if not image_source or not image_license:
            raise ValueError("image_source and image_license are required when image_path is supplied")
        content, _mime, extension = read_image(image_root, image_path)
        digest = hashlib.sha256(content).hexdigest()
        filename = f"{digest}{extension}"
        existing = db.scalar(select(PartImage).where(
            PartImage.part_id == part.id, PartImage.filename == filename,
        ))
        if existing is None:
            IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            target = IMAGE_DIR / filename
            if not target.exists():
                target.write_bytes(content)
            db.add(PartImage(
                part_id=part.id, filename=filename,
                alt_text=clean(row, "image_alt_text"), source=image_source,
                license=image_license,
            ))
            db.flush()
            report.images_added += 1
    report.rows_imported += 1


def import_csv(csv_path: Path, *, apply: bool, image_root: Path, batch_size: int = 250) -> ImportReport:
    report = ImportReport()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV must contain a header row")
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError("CSV missing required columns: " + ", ".join(sorted(missing)))
        db = SessionLocal()
        batch_rows = 0
        try:
            for line_number, raw in enumerate(reader, start=2):
                report.rows_read += 1
                row = {key: value for key, value in raw.items() if key is not None}
                if not apply:
                    # Validate every record and its local image without writing to the database.
                    try:
                        validate_row(row, image_root)
                        report.rows_imported += 1
                    except (ValueError, OSError) as exc:
                        report.rows_skipped += 1
                        report.errors.append({"line": line_number, "error": str(exc)})
                    continue
                try:
                    process_row(row, db, image_root, report)
                    batch_rows += 1
                    if batch_rows >= batch_size:
                        db.commit()
                        batch_rows = 0
                except (ValueError, OSError) as exc:
                    db.rollback()
                    batch_rows = 0
                    report.rows_skipped += 1
                    report.errors.append({"line": line_number, "error": str(exc)})
            if apply and batch_rows:
                db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise RuntimeError(f"Database import failed: {exc}") from exc
        finally:
            db.close()
    return report


def validate_row(row: dict[str, str | None], image_root: Path) -> None:
    for key in row:
        clean(row, key)
    if not clean(row, "part_number") or not clean(row, "title") or not clean(row, "part_source"):
        raise ValueError("part_number, title, and part_source are required")
    vehicle_values = {name: clean(row, name) for name in VEHICLE_COLUMNS - {"year_start", "year_end"}}
    start_raw, end_raw = clean(row, "year_start"), clean(row, "year_end")
    if any(vehicle_values.values()) or start_raw is not None or end_raw is not None:
        if not vehicle_values.get("make") or not vehicle_values.get("model"):
            raise ValueError("make and model are required for a fitment")
        start, end = parse_year(start_raw, "year_start"), parse_year(end_raw, "year_end")
        if end < start:
            raise ValueError("year_end must be greater than or equal to year_start")
    if clean(row, "image_path"):
        if not clean(row, "image_source") or not clean(row, "image_license"):
            raise ValueError("image_source and image_license are required when image_path is supplied")
        read_image(image_root, clean(row, "image_path"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Import authorized catalog records from a local CSV file.")
    parser.add_argument("csv_file", type=Path, help="CSV containing normalized catalog rows")
    parser.add_argument("--images-dir", type=Path, default=Path("."), help="Root directory for local image_path values")
    parser.add_argument("--apply", action="store_true", help="Write valid rows to the local catalog database; default is dry-run")
    parser.add_argument("--batch-size", type=int, default=250, help="Rows per database transaction (default: 250)")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if not args.csv_file.is_file():
        parser.error(f"CSV file not found: {args.csv_file}")
    try:
        report = import_csv(args.csv_file, apply=args.apply, image_root=args.images_dir, batch_size=args.batch_size)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **asdict(report)}, indent=2))
    return 1 if report.rows_skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
