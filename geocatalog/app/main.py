import json
import math
import os
from contextlib import closing
from typing import Annotated

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row

app = FastAPI(title="Geological Library Map", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def get_connection():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url, row_factory=dict_row)
    required = ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"Database connection settings are missing: {', '.join(missing)}")
    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        row_factory=dict_row,
    )


def parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    try:
        west, south, east, north = map(float, bbox.split(","))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="bbox must be west,south,east,north") from exc
    if (
        not all(map(math.isfinite, (west, south, east, north)))
        or not all(-180 <= value <= 180 for value in (west, east))
        or not all(-90 <= value <= 90 for value in (south, north))
        or west >= east
        or south >= north
    ):
        raise HTTPException(status_code=422, detail="bbox coordinates are out of range or reversed")
    return west, south, east, north


def decode_geojson(value):
    return json.loads(value) if isinstance(value, str) else value


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


@app.get("/healthz")
def healthz():
    try:
        with closing(get_connection()) as connection:
            connection.execute("SELECT PostGIS_Version()")
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}


@app.get("/api/features")
def search_features(
    q: Annotated[str | None, Query(max_length=200)] = None,
    region: Annotated[str | None, Query(max_length=200)] = None,
    year_from: Annotated[int | None, Query(ge=1500, le=2200)] = None,
    year_to: Annotated[int | None, Query(ge=1500, le=2200)] = None,
    bbox: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
):
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(status_code=422, detail="year_from must not exceed year_to")
    bounds = parse_bbox(bbox)
    conditions = []
    params = []
    if q:
        conditions.append(
            "(d.search_vector @@ websearch_to_tsquery('simple', %s) "
            "OR d.search_vector @@ websearch_to_tsquery('russian', %s) "
            "OR d.search_vector @@ websearch_to_tsquery('english', %s))"
        )
        params.extend((q, q, q))
    if region:
        conditions.append("d.region ILIKE %s")
        params.append(f"%{region}%")
    if year_from is not None:
        conditions.append("d.year >= %s")
        params.append(year_from)
    if year_to is not None:
        conditions.append("d.year <= %s")
        params.append(year_to)
    if bounds:
        conditions.append("ST_Intersects(f.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))")
        params.extend(bounds)
    where_clause = " AND ".join(conditions) if conditions else "TRUE"
    query = f"""
        SELECT f.id, f.name, f.kind, f.metadata, ST_AsGeoJSON(f.geom) AS geometry,
               json_agg(json_build_object(
                   'id', d.id,
                   'title', d.title,
                   'inventory_number', d.inventory_number,
                   'region', d.region,
                   'year', d.year,
                   'topic', d.topic,
                   'description', d.description,
                   'archive_reference', d.archive_reference
               ) ORDER BY d.year DESC NULLS LAST, d.title) AS documents
        FROM features f
        JOIN feature_documents fd ON fd.feature_id = f.id
        JOIN documents d ON d.id = fd.document_id
        WHERE {where_clause}
        GROUP BY f.id
        ORDER BY f.name
        LIMIT %s
    """
    with closing(get_connection()) as connection:
        rows = connection.execute(query, [*params, limit]).fetchall()
    features = []
    for row in rows:
        features.append(
            {
                "type": "Feature",
                "id": row["id"],
                "geometry": decode_geojson(row["geometry"]),
                "properties": {
                    "name": row["name"],
                    "kind": row["kind"],
                    "metadata": row["metadata"],
                    "documents": row["documents"],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/features/{feature_id}")
def get_feature(feature_id: int):
    with closing(get_connection()) as connection:
        row = connection.execute(
            """
            SELECT f.id, f.name, f.kind, f.metadata, ST_AsGeoJSON(f.geom) AS geometry,
                   coalesce(json_agg(json_build_object(
                       'id', d.id,
                       'title', d.title,
                       'inventory_number', d.inventory_number,
                       'region', d.region,
                       'year', d.year,
                       'topic', d.topic,
                       'description', d.description,
                       'archive_reference', d.archive_reference
                   ) ORDER BY d.year DESC NULLS LAST, d.title)
                   FILTER (WHERE d.id IS NOT NULL), '[]'::json) AS documents
            FROM features f
            LEFT JOIN feature_documents fd ON fd.feature_id = f.id
            LEFT JOIN documents d ON d.id = fd.document_id
            WHERE f.id = %s
            GROUP BY f.id
            """,
            (feature_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="feature not found")
    return {
        "type": "Feature",
        "id": row["id"],
        "geometry": decode_geojson(row["geometry"]),
        "properties": {
            "name": row["name"],
            "kind": row["kind"],
            "metadata": row["metadata"],
            "documents": row["documents"],
        },
    }
