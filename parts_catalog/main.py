"""Starter parts-catalog API for authorized catalog data.

Run with: uvicorn parts_catalog.main:app --reload
"""
from __future__ import annotations

import hmac
import os
import re
import uuid
from pathlib import Path
from typing import Generator

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import (  # type: ignore[import-untyped]
    ForeignKey,
    UniqueConstraint,
    create_engine,
    or_,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("CATALOG_DATA_DIR", ROOT / "data")).resolve()
IMAGE_DIR = DATA_DIR / "images"
DATABASE_URL = os.getenv("CATALOG_DATABASE_URL", f"sqlite:///{DATA_DIR / 'catalog.db'}")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGES = {
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/webp": (".webp", b"RIFF"),
}

DATA_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (UniqueConstraint("make", "model", "year_start", "year_end", "engine", name="uq_vehicle_application"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    make: Mapped[str] = mapped_column(index=True)
    model: Mapped[str] = mapped_column(index=True)
    year_start: Mapped[int]
    year_end: Mapped[int]
    engine: Mapped[str | None] = mapped_column(default=None)
    engine_code: Mapped[str | None] = mapped_column(default=None)
    region: Mapped[str | None] = mapped_column(default=None)
    source: Mapped[str | None] = mapped_column(default=None)
    fitments: Mapped[list["Fitment"]] = relationship(back_populates="vehicle", cascade="all, delete-orphan")


class Part(Base):
    __tablename__ = "parts"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_number: Mapped[str] = mapped_column(unique=True, index=True)
    brand: Mapped[str | None] = mapped_column(default=None, index=True)
    title: Mapped[str] = mapped_column(index=True)
    description: Mapped[str | None] = mapped_column(default=None)
    category: Mapped[str | None] = mapped_column(default=None, index=True)
    source: Mapped[str | None] = mapped_column(default=None)
    fitments: Mapped[list["Fitment"]] = relationship(back_populates="part", cascade="all, delete-orphan")
    images: Mapped[list["PartImage"]] = relationship(back_populates="part", cascade="all, delete-orphan")


class Fitment(Base):
    __tablename__ = "fitments"
    __table_args__ = (UniqueConstraint("part_id", "vehicle_id", "notes", name="uq_part_vehicle_notes"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id", ondelete="CASCADE"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), index=True)
    notes: Mapped[str | None] = mapped_column(default=None)
    source: Mapped[str | None] = mapped_column(default=None)
    part: Mapped[Part] = relationship(back_populates="fitments")
    vehicle: Mapped[Vehicle] = relationship(back_populates="fitments")


class PartImage(Base):
    __tablename__ = "part_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(unique=True)
    alt_text: Mapped[str | None] = mapped_column(default=None)
    source: Mapped[str | None] = mapped_column(default=None)
    license: Mapped[str | None] = mapped_column(default=None)
    part: Mapped[Part] = relationship(back_populates="images")


Base.metadata.create_all(engine)
app = FastAPI(title="Parts Catalog Lookup", version="0.1.0", description="Vehicle, part-number, fitment, and image lookup API.")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("CATALOG_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Admin writes are disabled until CATALOG_ADMIN_TOKEN is configured.")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="A valid X-Admin-Token header is required.")


class VehicleInput(BaseModel):
    make: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=100)
    year_start: int = Field(ge=1886, le=2200)
    year_end: int = Field(ge=1886, le=2200)
    engine: str | None = Field(default=None, max_length=120)
    engine_code: str | None = Field(default=None, max_length=80)
    region: str | None = Field(default=None, max_length=80)
    source: str | None = Field(default=None, max_length=500)


class PartInput(BaseModel):
    part_number: str = Field(min_length=1, max_length=100)
    brand: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    source: str | None = Field(default=None, max_length=500)


class FitmentInput(BaseModel):
    part_id: int = Field(gt=0)
    vehicle_id: int = Field(gt=0)
    notes: str | None = Field(default=None, max_length=1000)
    source: str | None = Field(default=None, max_length=500)


def serialize_vehicle(vehicle: Vehicle) -> dict:
    return {
        "id": vehicle.id, "make": vehicle.make, "model": vehicle.model,
        "year_start": vehicle.year_start, "year_end": vehicle.year_end,
        "engine": vehicle.engine, "engine_code": vehicle.engine_code,
        "region": vehicle.region, "source": vehicle.source,
    }


def serialize_part(part: Part, fitments: list[Fitment] | None = None) -> dict:
    related = fitments if fitments is not None else part.fitments
    return {
        "id": part.id, "part_number": part.part_number, "brand": part.brand,
        "title": part.title, "description": part.description, "category": part.category,
        "source": part.source,
        "images": [
            {"url": f"/media/{image.filename}", "alt_text": image.alt_text,
             "source": image.source, "license": image.license}
            for image in part.images
        ],
        "fitments": [
            {"id": fitment.id, "notes": fitment.notes, "source": fitment.source,
             "vehicle": serialize_vehicle(fitment.vehicle)}
            for fitment in related
        ],
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/vehicles")
def list_vehicles(
    make: str | None = None,
    model: str | None = None,
    year: int | None = Query(default=None, ge=1886, le=2200),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(Vehicle)
    if make:
        stmt = stmt.where(Vehicle.make.ilike(f"%{make.strip()}%"))
    if model:
        stmt = stmt.where(Vehicle.model.ilike(f"%{model.strip()}%"))
    if year is not None:
        stmt = stmt.where(Vehicle.year_start <= year, Vehicle.year_end >= year)
    stmt = stmt.order_by(Vehicle.make, Vehicle.model, Vehicle.year_start).limit(500)
    return [serialize_vehicle(vehicle) for vehicle in db.scalars(stmt).all()]


@app.get("/api/parts")
def search_parts(
    q: str = Query(min_length=1, max_length=120),
    vehicle_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    term = q.strip()
    if not term:
        raise HTTPException(status_code=422, detail="Search term cannot be blank.")
    stmt = select(Part).where(
        or_(Part.part_number.ilike(f"%{term}%"), Part.brand.ilike(f"%{term}%"), Part.title.ilike(f"%{term}%"))
    )
    if vehicle_id is not None:
        stmt = stmt.join(Fitment).where(Fitment.vehicle_id == vehicle_id)
    parts = db.scalars(stmt.order_by(Part.part_number).limit(limit)).unique().all()
    return [serialize_part(part) for part in parts]


@app.get("/api/parts/{part_id}")
def get_part(part_id: int, db: Session = Depends(get_db)) -> dict:
    part = db.get(Part, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found.")
    return serialize_part(part)


@app.post("/api/admin/vehicles", dependencies=[Depends(require_admin)], status_code=201)
def create_vehicle(payload: VehicleInput, db: Session = Depends(get_db)) -> dict:
    if payload.year_end < payload.year_start:
        raise HTTPException(status_code=422, detail="year_end must be greater than or equal to year_start.")
    vehicle = Vehicle(**payload.model_dump())
    db.add(vehicle)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Vehicle already exists or could not be saved.") from exc
    db.refresh(vehicle)
    return serialize_vehicle(vehicle)


@app.post("/api/admin/parts", dependencies=[Depends(require_admin)], status_code=201)
def create_part(payload: PartInput, db: Session = Depends(get_db)) -> dict:
    values = payload.model_dump()
    values["part_number"] = values["part_number"].strip()
    part = Part(**values)
    db.add(part)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Part number already exists or could not be saved.") from exc
    db.refresh(part)
    return serialize_part(part)


@app.post("/api/admin/fitments", dependencies=[Depends(require_admin)], status_code=201)
def create_fitment(payload: FitmentInput, db: Session = Depends(get_db)) -> dict:
    part = db.get(Part, payload.part_id)
    vehicle = db.get(Vehicle, payload.vehicle_id)
    if not part or not vehicle:
        raise HTTPException(status_code=404, detail="The part or vehicle does not exist.")
    fitment = Fitment(**payload.model_dump())
    db.add(fitment)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Fitment already exists or could not be saved.") from exc
    db.refresh(fitment)
    return {"id": fitment.id, "part_id": fitment.part_id, "vehicle_id": fitment.vehicle_id,
            "notes": fitment.notes, "source": fitment.source}


@app.post("/api/admin/parts/{part_id}/images", dependencies=[Depends(require_admin)], status_code=201)
async def upload_part_image(
    part_id: int,
    file: UploadFile = File(...),
    alt_text: str | None = None,
    source: str | None = None,
    license: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    part = db.get(Part, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found.")
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_IMAGES:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG, and WebP images are accepted.")
    contents = await file.read(MAX_IMAGE_BYTES + 1)
    if not contents or len(contents) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image must be non-empty and no larger than 5 MiB.")
    extension, signature = ALLOWED_IMAGES[content_type]
    if not contents.startswith(signature) or (content_type == "image/webp" and contents[8:12] != b"WEBP"):
        raise HTTPException(status_code=415, detail="The uploaded file content does not match its image type.")
    filename = f"{uuid.uuid4().hex}{extension}"
    path = IMAGE_DIR / filename
    path.write_bytes(contents)
    image = PartImage(part_id=part_id, filename=filename, alt_text=alt_text, source=source, license=license)
    db.add(image)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not save image metadata.") from exc
    return {"url": f"/media/{filename}", "alt_text": alt_text, "source": source, "license": license}


@app.get("/media/{filename}")
def get_image(filename: str) -> FileResponse:
    if not re.fullmatch(r"[a-f0-9]{32}\.(jpg|png|webp)", filename):
        raise HTTPException(status_code=404, detail="Image not found.")
    path = IMAGE_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(path)


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Parts Catalog Lookup</title><style>
body{font:16px system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#172033}h1{margin-bottom:.3rem}
form,.filters{display:flex;gap:.6rem;flex-wrap:wrap;margin:1rem 0}input,select,button{font:inherit;padding:.65rem;border:1px solid #bbc3d0;border-radius:6px}
input{flex:1;min-width:180px}button{background:#173f70;color:white;cursor:pointer}.card{border:1px solid #d5dbe4;border-radius:8px;padding:1rem;margin:.75rem 0}
.muted{color:#596579;font-size:.92rem}img{max-width:150px;max-height:120px;object-fit:contain;display:block;margin:.5rem 0}
</style></head><body><h1>Parts Catalog Lookup</h1><p class="muted">Search the catalog data configured by its owner. Fitment is only as complete as the data source.</p>
<div class="filters"><select id="vehicle"><option value="">All vehicles</option></select><input id="q" placeholder="Part number, brand, or description" maxlength="120"><button id="search">Search</button></div>
<div id="status" class="muted">Loading vehicle applications…</div><div id="results"></div>
<script>
const vehicle=document.querySelector('#vehicle'), q=document.querySelector('#q'), results=document.querySelector('#results'), status=document.querySelector('#status');
async function loadVehicles(){try{const r=await fetch('/api/vehicles');if(!r.ok)throw Error('Could not load vehicles');const rows=await r.json();for(const v of rows){const o=document.createElement('option');o.value=v.id;o.textContent=`${v.make} ${v.model} (${v.year_start}–${v.year_end})${v.engine?' · '+v.engine:''}`;vehicle.append(o)}status.textContent=rows.length?`${rows.length} vehicle applications loaded.`:'No vehicle data yet. Import authorized catalog records to get started.'}catch(e){status.textContent=e.message}}
async function search(){const term=q.value.trim();if(!term){status.textContent='Enter a part number, brand, or description.';return}status.textContent='Searching…';results.replaceChildren();const params=new URLSearchParams({q:term});if(vehicle.value)params.set('vehicle_id',vehicle.value);try{const r=await fetch('/api/parts?'+params);const rows=await r.json();if(!r.ok)throw Error(rows.detail||'Search failed');status.textContent=`${rows.length} result(s)`;for(const p of rows){const card=document.createElement('article');card.className='card';const h=document.createElement('h2');h.textContent=p.part_number+' — '+p.title;card.append(h);const details=document.createElement('p');details.textContent=[p.brand,p.category,p.description].filter(Boolean).join(' · ');card.append(details);for(const image of p.images){const img=document.createElement('img');img.src=image.url;img.alt=image.alt_text||p.title;card.append(img)}for(const f of p.fitments){const line=document.createElement('p');line.className='muted';line.textContent=`Fits: ${f.vehicle.make} ${f.vehicle.model} ${f.vehicle.year_start}–${f.vehicle.year_end}${f.vehicle.engine?' · '+f.vehicle.engine:''}${f.notes?' · '+f.notes:''}`;card.append(line)}results.append(card)}}catch(e){status.textContent=e.message}}
document.querySelector('#search').addEventListener('click',search);q.addEventListener('keydown',e=>{if(e.key==='Enter')search()});vehicle.addEventListener('change',()=>{if(q.value.trim())search()});loadVehicles();
</script></body></html>"""
