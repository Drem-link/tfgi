import csv
import io
import json
import math
import os
import re
import secrets
import time
import zipfile
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

password_hasher = PasswordHasher()
login_failures: dict[str, list[float]] = {}
session_max_age = 8 * 60 * 60
login_window = 15 * 60
login_failure_limit = 8
max_file_size = 50 * 1024 * 1024
max_upload_files = 10
max_request_size = 100 * 1024 * 1024
upload_root = Path(os.environ.get("UPLOAD_DIR", "/uploads"))
allowed_file_types = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".csv": "text/csv",
}


def auth_config() -> tuple[str, str, URLSafeTimedSerializer]:
    username = os.environ.get("AUTH_USERNAME", "").strip()
    password_hash = os.environ.get("AUTH_PASSWORD_HASH", "").strip()
    session_secret = os.environ.get("SESSION_SECRET", "").strip()
    if not username or not password_hash or len(session_secret) < 32:
        raise RuntimeError("AUTH_USERNAME, AUTH_PASSWORD_HASH and a 32-character SESSION_SECRET are required")
    return username, password_hash, URLSafeTimedSerializer(session_secret, salt="geocatalog-session-v1")


def ensure_users_table() -> None:
    username, password_hash, _ = auth_config()
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS geocatalog_users (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                username TEXT NOT NULL UNIQUE
                    CHECK (username ~ '^[A-Za-z0-9._@-]{1,128}$'),
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                token_version INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute(
            """
            INSERT INTO geocatalog_users (username, password_hash, role)
            VALUES (%s, %s, 'admin')
            ON CONFLICT (username) DO NOTHING
            """,
            (username, password_hash),
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS document_files (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                storage_key TEXT NOT NULL UNIQUE CHECK (storage_key ~ '^[a-f0-9]{48}\\.[a-z0-9]+$'),
                original_filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes BIGINT NOT NULL CHECK (size_bytes BETWEEN 1 AND 52428800),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS document_files_document_idx ON document_files (document_id)")


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    ensure_users_table()
    yield


app = FastAPI(title="Geological Library Map", version="0.1.0", lifespan=app_lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def authenticated_user(request: Request) -> dict | None:
    try:
        _, _, serializer = auth_config()
        session = serializer.loads(
            request.cookies.get("geocatalog_session", ""),
            max_age=session_max_age,
        )
    except (RuntimeError, BadSignature, SignatureExpired, TypeError):
        return None
    if not isinstance(session, dict) or not isinstance(session.get("id"), int):
        return None
    with closing(get_connection()) as connection:
        user = connection.execute(
            """
            SELECT id, username, role
            FROM geocatalog_users
            WHERE id = %s AND token_version = %s AND is_active
            """,
            (session["id"], session.get("version")),
        ).fetchone()
    if user is None:
        return None
    return user


def is_public_path(path: str) -> bool:
    return path in {"/login", "/auth/login", "/healthz", "/livez", "/static/login.css", "/static/login.js"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    is_upload = request.url.path == "/api/admin/documents" and request.method == "POST"
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
            if is_upload and user["role"] != "admin":
                response = JSONResponse({"detail": "administrator permission required"}, status_code=403)
            elif is_upload and request.headers.get("content-length") is None:
                response = JSONResponse({"detail": "Content-Length is required for file uploads"}, status_code=411)
            elif is_upload and not request.headers.get("content-length", "").isdigit():
                response = JSONResponse({"detail": "invalid Content-Length"}, status_code=400)
            elif is_upload and int(request.headers["content-length"]) > max_request_size + 5 * 1024 * 1024:
                response = JSONResponse({"detail": "upload request cannot exceed 105 MiB"}, status_code=413)
            elif request.url.path.startswith("/api/admin/") and user["role"] != "admin":
                response = JSONResponse({"detail": "administrator permission required"}, status_code=403)
            elif request.url.path.startswith("/admin") and user["role"] != "admin":
                response = RedirectResponse("/", status_code=303)
            else:
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


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=8, max_length=1024)
    role: str = Field(pattern=r"^(admin|viewer)$")


class UserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(admin|viewer)$")
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=1024)

    @model_validator(mode="after")
    def require_update(self):
        if self.role is None and self.active is None and self.password is None:
            raise ValueError("at least one of role, active, or password is required")
        return self


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="invalid request origin")
    if not origin and request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="cross-site request denied")


def parse_coordinates(value: str) -> tuple[float, float]:
    try:
        longitude, latitude = map(float, value.split(","))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="each vertex must be longitude,latitude") from exc
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise HTTPException(status_code=422, detail="coordinates must be finite numbers")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise HTTPException(status_code=422, detail="coordinates are outside longitude/latitude ranges")
    return longitude, latitude


def parse_polygon_vertices(value: str) -> list[tuple[float, float]]:
    vertices = [parse_coordinates(pair.strip()) for pair in value.split(";") if pair.strip()]
    if len(vertices) < 3:
        raise HTTPException(status_code=422, detail="an area needs at least three vertices")
    if vertices[-1] != vertices[0]:
        vertices.append(vertices[0])
    if len(set(vertices[:-1])) < 3:
        raise HTTPException(status_code=422, detail="an area needs at least three distinct vertices")
    return vertices


def validate_uploaded_file(filename: str | None, data: bytes) -> tuple[str, str, str]:
    if not filename:
        raise HTTPException(status_code=422, detail="every uploaded file must have a filename")
    normalized_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not normalized_name or len(normalized_name) > 255 or any(ord(char) < 32 for char in normalized_name):
        raise HTTPException(status_code=422, detail="invalid filename")
    extension = Path(normalized_name).suffix.lower()
    media_type = allowed_file_types.get(extension)
    if media_type is None:
        raise HTTPException(status_code=415, detail=f"file type {extension or '(no extension)'} is not allowed")
    if not data or len(data) > max_file_size:
        raise HTTPException(status_code=413, detail="each file must be between 1 byte and 50 MiB")

    valid = False
    if extension == ".pdf":
        valid = data.startswith(b"%PDF-")
    elif extension in {".jpg", ".jpeg"}:
        valid = data.startswith(b"\xff\xd8\xff")
    elif extension == ".png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension in {".tif", ".tiff"}:
        valid = data.startswith((b"II*\x00", b"MM\x00*"))
    elif extension in {".docx", ".xlsx", ".pptx"}:
        expected_part = {
            ".docx": "word/",
            ".xlsx": "xl/",
            ".pptx": "ppt/",
        }[extension]
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                valid = "[Content_Types].xml" in names and any(name.startswith(expected_part) for name in names)
        except (zipfile.BadZipFile, OSError):
            valid = False
    elif extension in {".txt", ".csv"}:
        try:
            text_data = data.decode("utf-8-sig")
            valid = "\x00" not in text_data
            if valid and extension == ".csv":
                next(csv.reader(io.StringIO(text_data)), None)
        except (UnicodeDecodeError, csv.Error):
            valid = False

    if not valid:
        raise HTTPException(status_code=415, detail=f"file contents do not match {extension}")
    return normalized_name, media_type, extension[1:]


def require_admin(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if user is None or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="administrator permission required")
    return user


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


def attach_document_files(connection, feature_rows) -> None:
    documents = [document for row in feature_rows for document in row["documents"]]
    document_ids = [document["id"] for document in documents]
    if not document_ids:
        return
    files = connection.execute(
        """
        SELECT id, document_id, original_filename, size_bytes
        FROM document_files
        WHERE document_id = ANY(%s)
        ORDER BY original_filename
        """,
        (document_ids,),
    ).fetchall()
    files_by_document: dict[int, list[dict]] = {}
    for file in files:
        files_by_document.setdefault(file["document_id"], []).append({
            "id": file["id"],
            "filename": file["original_filename"],
            "size_bytes": file["size_bytes"],
        })
    for document in documents:
        document["files"] = files_by_document.get(document["id"], [])


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


@app.get("/login")
def login_page():
    return FileResponse("app/static/login.html")


@app.get("/admin/users")
def users_page(request: Request):
    require_admin(request)
    return FileResponse("app/static/users.html")


@app.get("/admin/catalog")
def catalog_admin_page(request: Request):
    require_admin(request)
    return FileResponse("app/static/catalog-admin.html")


@app.get("/api/session")
def get_session(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return {"username": user["username"], "role": user["role"]}


@app.get("/api/admin/users")
def list_users(request: Request):
    require_admin(request)
    with closing(get_connection()) as connection:
        users = connection.execute(
            """
            SELECT id, username, role, is_active, created_at
            FROM geocatalog_users
            ORDER BY username
            """
        ).fetchall()
    return {"users": users}


@app.post("/api/admin/users", status_code=201)
def create_user(user: UserCreate, request: Request):
    require_admin(request)
    try:
        with get_connection() as connection:
            created = connection.execute(
                """
                INSERT INTO geocatalog_users (username, password_hash, role)
                VALUES (%s, %s, %s)
                RETURNING id, username, role, is_active, created_at
                """,
                (user.username, password_hasher.hash(user.password), user.role),
            ).fetchone()
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="username already exists") from exc
    return created


@app.patch("/api/admin/users/{user_id}")
def update_user(user_id: int, update: UserUpdate, request: Request):
    require_admin(request)
    changes = update.model_dump(exclude_unset=True)
    with get_connection() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(73412905)")
        current = connection.execute(
            "SELECT id, username, role, is_active FROM geocatalog_users WHERE id = %s FOR UPDATE",
            (user_id,),
        ).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="user not found")

        next_role = changes.get("role", current["role"])
        next_active = changes.get("active", current["is_active"])
        if current["role"] == "admin" and current["is_active"] and (
            next_role != "admin" or not next_active
        ):
            admin_count = connection.execute(
                "SELECT count(*) AS count FROM geocatalog_users WHERE role = 'admin' AND is_active"
            ).fetchone()["count"]
            if admin_count <= 1:
                raise HTTPException(status_code=409, detail="cannot disable or demote the last active administrator")

        assignments = []
        values = []
        if "role" in changes:
            assignments.append("role = %s")
            values.append(changes["role"])
        if "active" in changes:
            assignments.append("is_active = %s")
            values.append(changes["active"])
        if "password" in changes:
            assignments.extend(("password_hash = %s", "token_version = token_version + 1"))
            values.append(password_hasher.hash(changes["password"]))
        values.append(user_id)
        updated = connection.execute(
            f"""
            UPDATE geocatalog_users
            SET {", ".join(assignments)}
            WHERE id = %s
            RETURNING id, username, role, is_active, created_at
            """,
            values,
        ).fetchone()
    return updated


@app.get("/api/admin/features")
def list_features_for_admin(request: Request):
    require_admin(request)
    with closing(get_connection()) as connection:
        features = connection.execute(
            "SELECT id, name, kind FROM features ORDER BY name"
        ).fetchall()
    return {"features": features}


@app.get("/api/admin/files")
def list_files_for_admin(request: Request):
    require_admin(request)
    with closing(get_connection()) as connection:
        files = connection.execute(
            """
            SELECT file.id, file.original_filename, file.size_bytes, document.title AS document_title,
                   feature.name AS feature_name
            FROM document_files file
            JOIN documents document ON document.id = file.document_id
            JOIN feature_documents link ON link.document_id = document.id
            JOIN features feature ON feature.id = link.feature_id
            ORDER BY file.created_at DESC, file.original_filename
            """
        ).fetchall()
    return {"files": files}


@app.delete("/api/admin/files/{file_id}", status_code=204)
def delete_uploaded_file(file_id: int, request: Request):
    require_admin(request)
    require_same_origin(request)
    with get_connection() as connection:
        file = connection.execute(
            "DELETE FROM document_files WHERE id = %s RETURNING storage_key",
            (file_id,),
        ).fetchone()
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    if not re.fullmatch(r"[a-f0-9]{48}\.[a-z0-9]+", file["storage_key"]):
        raise HTTPException(status_code=500, detail="invalid stored file key")
    (upload_root / file["storage_key"]).unlink(missing_ok=True)
    return None


@app.post("/api/admin/documents", status_code=201)
async def create_document_with_files(
    request: Request,
    feature_mode: Annotated[str, Form()],
    document_title: Annotated[str, Form(min_length=1, max_length=500)],
    files: Annotated[list[UploadFile], File()],
    feature_id: Annotated[str, Form()] = "",
    feature_name: Annotated[str, Form(max_length=500)] = "",
    geometry_kind: Annotated[str, Form()] = "well",
    longitude: Annotated[str, Form()] = "",
    latitude: Annotated[str, Form()] = "",
    polygon_vertices: Annotated[str, Form(max_length=20000)] = "",
    feature_metadata: Annotated[str, Form(max_length=10000)] = "{}",
    inventory_number: Annotated[str, Form(max_length=200)] = "",
    region: Annotated[str, Form(max_length=200)] = "",
    document_year: Annotated[str, Form()] = "",
    topic: Annotated[str, Form(max_length=1000)] = "",
    description: Annotated[str, Form(max_length=10000)] = "",
    archive_reference: Annotated[str, Form(max_length=1000)] = "",
):
    require_admin(request)
    require_same_origin(request)
    if feature_mode not in {"new", "existing"}:
        raise HTTPException(status_code=422, detail="feature_mode must be new or existing")
    title = document_title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="document title cannot be blank")
    if not files or len(files) > max_upload_files:
        raise HTTPException(status_code=413, detail="select between 1 and 10 files")

    year = None
    if document_year.strip():
        try:
            year = int(document_year)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="document year must be a number") from exc
        if not 1500 <= year <= 2200:
            raise HTTPException(status_code=422, detail="document year must be between 1500 and 2200")

    prepared_files = []
    request_size = 0
    for upload in files:
        data = await upload.read(max_file_size + 1)
        filename, media_type, extension = validate_uploaded_file(upload.filename, data)
        request_size += len(data)
        if request_size > max_request_size:
            raise HTTPException(status_code=413, detail="total file size per upload cannot exceed 100 MiB")
        storage_key = f"{secrets.token_hex(24)}.{extension}"
        prepared_files.append((filename, media_type, storage_key, data))

    if feature_mode == "existing":
        try:
            selected_feature_id = int(feature_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="select an existing catalog object") from exc
        new_feature_name = None
        geometry_wkt = None
        metadata = None
    else:
        new_feature_name = feature_name.strip()
        if not new_feature_name:
            raise HTTPException(status_code=422, detail="new catalog object needs a name")
        if geometry_kind == "well":
            try:
                longitude_value, latitude_value = parse_coordinates(f"{longitude},{latitude}")
            except HTTPException:
                raise
            geometry_wkt = None
        elif geometry_kind == "area":
            vertices = parse_polygon_vertices(polygon_vertices)
            geometry_wkt = "POLYGON((" + ", ".join(f"{lon} {lat}" for lon, lat in vertices) + "))"
            longitude_value = latitude_value = None
        else:
            raise HTTPException(status_code=422, detail="geometry kind must be a point or area")
        try:
            metadata = json.loads(feature_metadata or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="feature metadata must be valid JSON") from exc
        if not isinstance(metadata, dict):
            raise HTTPException(status_code=422, detail="feature metadata must be a JSON object")
        selected_feature_id = None

    upload_root.mkdir(parents=True, exist_ok=True)
    saved_paths = []
    try:
        for _, _, storage_key, data in prepared_files:
            path = upload_root / storage_key
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            saved_paths.append(path)
            with os.fdopen(descriptor, "wb") as stored_file:
                stored_file.write(data)

        try:
            with get_connection() as connection:
                if feature_mode == "existing":
                    feature = connection.execute(
                        "SELECT id, name FROM features WHERE id = %s",
                        (selected_feature_id,),
                    ).fetchone()
                    if feature is None:
                        raise HTTPException(status_code=404, detail="catalog object not found")
                elif geometry_kind == "well":
                    feature = connection.execute(
                        """
                        INSERT INTO features (name, kind, geom, metadata)
                        VALUES (%s, 'well', ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)
                        RETURNING id, name
                        """,
                        (new_feature_name, longitude_value, latitude_value, json.dumps(metadata)),
                    ).fetchone()
                else:
                    valid = connection.execute(
                        "SELECT ST_IsValid(ST_GeomFromText(%s, 4326)) AS valid",
                        (geometry_wkt,),
                    ).fetchone()["valid"]
                    if not valid:
                        raise HTTPException(status_code=422, detail="area contour is self-intersecting or invalid")
                    feature = connection.execute(
                        """
                        INSERT INTO features (name, kind, geom, metadata)
                        VALUES (%s, 'area', ST_GeomFromText(%s, 4326), %s)
                        RETURNING id, name
                        """,
                        (new_feature_name, geometry_wkt, json.dumps(metadata)),
                    ).fetchone()

                document = connection.execute(
                    """
                    INSERT INTO documents (
                        title, inventory_number, region, year, topic, description, archive_reference
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        title,
                        inventory_number.strip() or None,
                        region.strip() or None,
                        year,
                        topic.strip() or None,
                        description.strip() or None,
                        archive_reference.strip() or None,
                    ),
                ).fetchone()
                connection.execute(
                    "INSERT INTO feature_documents (feature_id, document_id) VALUES (%s, %s)",
                    (feature["id"], document["id"]),
                )
                for filename, media_type, storage_key, data in prepared_files:
                    connection.execute(
                        """
                        INSERT INTO document_files (document_id, storage_key, original_filename, media_type, size_bytes)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (document["id"], storage_key, filename, media_type, len(data)),
                    )
        except UniqueViolation as exc:
            raise HTTPException(status_code=409, detail="object name or inventory number already exists") from exc
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise

    return {
        "feature_id": feature["id"],
        "feature_name": feature["name"],
        "document_id": document["id"],
        "files": [{"filename": filename, "size_bytes": len(data)} for filename, _, _, data in prepared_files],
    }


@app.get("/api/files/{file_id}")
def download_document_file(file_id: int):
    with closing(get_connection()) as connection:
        file = connection.execute(
            """
            SELECT storage_key, original_filename
            FROM document_files
            WHERE id = %s
            """,
            (file_id,),
        ).fetchone()
    if file is None or not re.fullmatch(r"[a-f0-9]{48}\.[a-z0-9]+", file["storage_key"]):
        raise HTTPException(status_code=404, detail="file not found")
    path = upload_root / file["storage_key"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="stored file is missing")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=file["original_filename"],
        headers={"Cache-Control": "private, no-store"},
    )


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
        _, _, serializer = auth_config()
        with closing(get_connection()) as connection:
            user = connection.execute(
                """
                SELECT id, username, password_hash, role, token_version
                FROM geocatalog_users
                WHERE username = %s AND is_active
                """,
                (username,),
            ).fetchone()
    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail="authentication is not configured") from exc
    try:
        password_ok = user is not None and password_hasher.verify(user["password_hash"], password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        password_ok = False
    if not password_ok:
        login_failures[client].append(now)
        response = RedirectResponse("/login?error=invalid", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        return response

    login_failures.pop(client, None)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "geocatalog_session",
        serializer.dumps({"id": user["id"], "version": user["token_version"]}),
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
        attach_document_files(connection, rows)
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
        if row is not None:
            attach_document_files(connection, [row])
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
