import json
import math
import os
import secrets
import time
from contextlib import closing
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

app = FastAPI(title="Geological Library Map", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
password_hasher = PasswordHasher()
login_failures: dict[str, list[float]] = {}
session_max_age = 8 * 60 * 60
login_window = 15 * 60
login_failure_limit = 8


def auth_config() -> tuple[str, str, URLSafeTimedSerializer]:
    username = os.environ.get("AUTH_USERNAME", "").strip()
    password_hash = os.environ.get("AUTH_PASSWORD_HASH", "").strip()
    session_secret = os.environ.get("SESSION_SECRET", "").strip()
    if not username or not password_hash or len(session_secret) < 32:
        raise RuntimeError("AUTH_USERNAME, AUTH_PASSWORD_HASH and a 32-character SESSION_SECRET are required")
    return username, password_hash, URLSafeTimedSerializer(session_secret, salt="geocatalog-session-v1")


def authenticated_user(request: Request) -> str | None:
    try:
        expected_username, _, serializer = auth_config()
        username = serializer.loads(
            request.cookies.get("geocatalog_session", ""),
            max_age=session_max_age,
        )
    except (RuntimeError, BadSignature, SignatureExpired, TypeError):
        return None
    if not isinstance(username, str):
        return None
    return username if secrets.compare_digest(username.encode(), expected_username.encode()) else None


def is_public_path(path: str) -> bool:
    return path in {"/login", "/auth/login", "/healthz", "/livez", "/static/login.css", "/static/login.js"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    if is_public_path(request.url.path):
        response = await call_next(request)
    else:
        user = authenticated_user(request)
        if user is None:
            if request.url.path.startswith("/api/"):
                response = JSONResponse({"detail": "authentication required"}, status_code=401)
            else:
                response = RedirectResponse("/login", status_code=303)
        else:
            request.state.user = user
            response = await call_next(request)
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self' data:; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    return response


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
        connect_timeout=5,
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


@app.get("/login")
def login_page():
    return FileResponse("app/static/login.html")


@app.post("/auth/login")
async def login(request: Request):
    if request.url.scheme != "https":
        raise HTTPException(status_code=426, detail="HTTPS is required for login")
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != host:
        raise HTTPException(status_code=403, detail="invalid request origin")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="form-encoded credentials are required")
    try:
        body = (await request.body()).decode("utf-8")
        form = parse_qs(body, keep_blank_values=True, strict_parsing=True)
        username = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid login form") from None
    if len(username) > 128 or len(password) > 1024:
        raise HTTPException(status_code=400, detail="invalid login form")

    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    for address, timestamps in list(login_failures.items()):
        recent = [timestamp for timestamp in timestamps if now - timestamp < login_window]
        if recent:
            login_failures[address] = recent
        else:
            login_failures.pop(address)
    recent_failures = [timestamp for timestamp in login_failures.get(client, []) if now - timestamp < login_window]
    login_failures[client] = recent_failures
    if len(recent_failures) >= login_failure_limit:
        raise HTTPException(status_code=429, detail="too many login attempts; try again later")

    try:
        expected_username, password_hash, serializer = auth_config()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="authentication is not configured") from exc
    try:
        password_ok = password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        password_ok = False
    username_ok = secrets.compare_digest(username.encode(), expected_username.encode())
    if not (username_ok and password_ok):
        login_failures[client].append(now)
        response = RedirectResponse("/login?error=invalid", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        return response

    login_failures.pop(client, None)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "geocatalog_session",
        serializer.dumps(expected_username),
        max_age=session_max_age,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/auth/logout")
def logout(request: Request):
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="invalid request origin")
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("geocatalog_session", path="/", secure=True, httponly=True, samesite="strict")
    return response


@app.get("/healthz")
def healthz():
    try:
        with closing(get_connection()) as connection:
            connection.execute("SELECT PostGIS_Version()")
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}


@app.get("/livez")
def livez():
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
