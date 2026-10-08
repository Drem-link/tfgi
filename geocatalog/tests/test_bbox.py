import os
import json
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from argon2 import PasswordHasher
from psycopg.errors import UniqueViolation
from openpyxl import Workbook

from app import auth_cli, main as main_module
from app.main import app, livez, parse_bbox


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeAuthStore:
    def __init__(self, username, password_hash):
        self.users = {
            1: {
                "id": 1,
                "username": username,
                "password_hash": password_hash,
                "role": "admin",
                "is_active": True,
                "token_version": 0,
                "created_at": datetime.now(timezone.utc),
            }
        }
        self.next_id = 2
        self.features = {}
        self.documents = {}
        self.files = {}
        self.feature_documents = []
        self.next_feature_id = 1
        self.next_document_id = 1
        self.next_file_id = 1
        self.geo_feature_row = None
        self.saved_searches = {}
        self.audit = []

    def connect(self):
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def close(self):
        return None

    def execute(self, query, params=()):
        sql = " ".join(query.lower().split())
        if sql.startswith(("create table", "create index", "create unique index", "alter table", "update features", "do $$")) or \
                "pg_advisory_xact_lock" in sql:
            return FakeResult([])
        if sql.startswith("insert into catalog_audit"):
            self.store.audit.append({"action": params[1], "entity_type": params[2], "entity_id": params[3]})
            return FakeResult([])
        if sql.startswith("select doc_relation.id"):
            return FakeResult([])
        if sql.startswith("select id from documents where import_fingerprint"):
            fingerprint = params[0]
            document = next(
                (item for item in self.store.documents.values()
                 if item.get("import_fingerprint") == fingerprint),
                None,
            )
            return FakeResult([{"id": document["id"]}] if document else [])
        if sql.startswith("select id, name, kind, source_crs, st_astext(source_geom)"):
            feature = next(
                (item for item in self.store.features.values() if item["name"] == params[0]),
                None,
            )
            return FakeResult([feature.copy()] if feature else [])
        if sql.startswith("select st_isvalid"):
            return FakeResult([{"valid": True}])
        if sql.startswith("select f.id, f.name, f.kind, f.metadata"):
            row = self.store.geo_feature_row
            return FakeResult([row.copy()] if row else [])
        if sql.startswith("select id, name from features where id"):
            feature = self.store.features.get(params[0])
            return FakeResult([feature.copy()] if feature else [])
        if sql.startswith("insert into features"):
            if "source_geom" in sql:
                name, kind = params[:2]
                if any(item["name"] == name for item in self.store.features.values()):
                    raise UniqueViolation("feature name already exists")
                feature = {
                    "id": self.store.next_feature_id,
                    "name": name,
                    "kind": kind,
                    "source_crs": params[-1],
                    "coordinates": tuple(params[5:7]),
                    "geom": tuple(params[2:4]) if params[-1] == "EPSG:4326" else None,
                }
                self.store.features[feature["id"]] = feature
                self.store.next_feature_id += 1
                return FakeResult([feature.copy()])
            if "values (%s, 'well'," in sql:
                name = params[0]
                if any(item["name"] == name for item in self.store.features.values()):
                    raise UniqueViolation("feature name already exists")
                feature = {"id": self.store.next_feature_id, "name": name}
            else:
                name = params[0]
                if any(item["name"] == name for item in self.store.features.values()):
                    raise UniqueViolation("feature name already exists")
                feature = {"id": self.store.next_feature_id, "name": name}
            self.store.features[feature["id"]] = feature
            self.store.next_feature_id += 1
            return FakeResult([feature.copy()])
        if sql.startswith("insert into documents"):
            (
                title, inventory_number, region, year, topic, description, archive_reference,
                *extra_fields,
            ) = params
            if inventory_number and any(
                item["inventory_number"] == inventory_number for item in self.store.documents.values()
            ):
                raise UniqueViolation("inventory number already exists")
            document = {
                "id": self.store.next_document_id,
                "title": title,
                "inventory_number": inventory_number,
                "region": region,
                "year": year,
                "topic": topic,
                "description": description,
                "archive_reference": archive_reference,
            }
            if extra_fields:
                document.update(dict(zip(
                    (
                        "tgf_number", "document_type", "authors", "coauthors", "executor_org", "work_year_start",
                                "work_year_end", "created_place", "minerals", "archive_disk_number",
                        "material_composition", "electronic_copy_status", "efgi_id", "efgi_url",
                                "import_fingerprint",
                    ),
                    extra_fields,
                )))
            self.store.documents[document["id"]] = document
            self.store.next_document_id += 1
            return FakeResult([{"id": document["id"]}])
        if sql.startswith("insert into feature_documents"):
            self.store.feature_documents.append(tuple(params))
            return FakeResult([])
        if sql.startswith("insert into document_files"):
            document_id, storage_key, filename, media_type, size_bytes = params
            file = {
                "id": self.store.next_file_id,
                "document_id": document_id,
                "storage_key": storage_key,
                "original_filename": filename,
                "media_type": media_type,
                "size_bytes": size_bytes,
            }
            self.store.files[file["id"]] = file
            self.store.next_file_id += 1
            return FakeResult([{"id": file["id"]}])
        if sql.startswith("select id, document_id, original_filename, size_bytes"):
            document_ids = set(params[0])
            return FakeResult(sorted(
                [
                    {
                        "id": file["id"],
                        "document_id": file["document_id"],
                        "original_filename": file["original_filename"],
                        "size_bytes": file["size_bytes"],
                    }
                    for file in self.store.files.values()
                    if file["document_id"] in document_ids
                ],
                key=lambda file: file["original_filename"],
            ))
        if sql.startswith("select storage_key, original_filename from document_files"):
            file = self.store.files.get(params[0])
            return FakeResult([file.copy()] if file else [])
        if sql.startswith("delete from document_files"):
            file = self.store.files.pop(params[0], None)
            return FakeResult([{"storage_key": file["storage_key"]}] if file else [])
        if sql.startswith("select file.id, file.original_filename"):
            result = []
            for file in self.store.files.values():
                document = self.store.documents[file["document_id"]]
                feature = next(iter(self.store.features.values()))
                result.append({
                    "id": file["id"],
                    "original_filename": file["original_filename"],
                    "size_bytes": file["size_bytes"],
                    "document_title": document["title"],
                    "feature_name": feature["name"],
                })
            return FakeResult(result)
        if "where username = %s and is_active" in sql:
            user = next(
                (item for item in self.store.users.values()
                 if item["username"] == params[0] and item["is_active"]),
                None,
            )
            return FakeResult([user.copy()] if user else [])
        if "where id = %s and token_version = %s and is_active" in sql:
            user = self.store.users.get(params[0])
            valid = user and user["token_version"] == params[1] and user["is_active"]
            return FakeResult([user.copy()] if valid else [])
        if "order by username" in sql and sql.startswith("select id, username, role"):
            return FakeResult([
                {key: user[key] for key in ("id", "username", "role", "is_active", "created_at")}
                for user in sorted(self.store.users.values(), key=lambda item: item["username"])
            ])
        if sql.startswith("insert into geocatalog_users"):
            if "on conflict (username) do nothing" in sql:
                username, password_hash = params
                role = "admin"
                if any(user["username"] == username for user in self.store.users.values()):
                    return FakeResult([])
            else:
                username, password_hash, role = params
            if any(user["username"] == username for user in self.store.users.values()):
                raise UniqueViolation("username already exists")
            user = {
                "id": self.store.next_id,
                "username": username,
                "password_hash": password_hash,
                "role": role,
                "is_active": True,
                "token_version": 0,
                "created_at": datetime.now(timezone.utc),
            }
            self.store.users[user["id"]] = user
            self.store.next_id += 1
            return FakeResult([{
                key: user[key] for key in ("id", "username", "role", "is_active", "created_at")
            }])
        if "for update" in sql:
            user = self.store.users.get(params[0])
            return FakeResult([user.copy()] if user else [])
        if "count(*) as count" in sql:
            return FakeResult([{
                "count": sum(user["role"] == "admin" and user["is_active"] for user in self.store.users.values())
            }])
        if sql.startswith("update geocatalog_users"):
            user_id = params[-1]
            user = self.store.users[user_id]
            assignments = sql.split(" set ", 1)[1].split(" where ", 1)[0].split(", ")
            value_index = 0
            for assignment in assignments:
                if assignment == "token_version = token_version + 1":
                    user["token_version"] += 1
                    continue
                key = assignment.split(" = ", 1)[0]
                user[key] = params[value_index]
                value_index += 1
            return FakeResult([{
                key: user[key] for key in ("id", "username", "role", "is_active", "created_at")
            }])
        raise AssertionError(f"unhandled fake SQL: {sql}")


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setattr(main_module, "ensure_users_table", lambda: None)
    monkeypatch.setenv("AUTH_USERNAME", "catalog-admin")
    password_hash = PasswordHasher().hash("a-strong-test-password")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-that-is-at-least-32-bytes")
    store = FakeAuthStore("catalog-admin", password_hash)
    monkeypatch.setattr(main_module, "get_connection", store.connect)
    with TestClient(app, base_url="https://catalog.test") as client:
        client.test_store = store
        yield client


def test_parse_bbox():
    assert parse_bbox("30,50,40,60") == (30.0, 50.0, 40.0, 60.0)


def test_livez_does_not_require_database():
    assert livez() == {"status": "ok"}


def test_offline_country_boundaries_are_bundled():
    geography = Path(__file__).parents[1] / "app/static/ne_110m_admin_0_countries.geojson"
    data = json.loads(geography.read_text(encoding="utf-8"))

    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) >= 170
    assert all(feature["geometry"]["type"] in {"Polygon", "MultiPolygon"} for feature in data["features"])
    assert all(feature["properties"].get("ADMIN") for feature in data["features"])


@pytest.mark.parametrize(
    ("filename", "content", "expected_type"),
    [
        ("report.pdf", b"%PDF-1.7 test", "application/pdf"),
        ("photo.JPG", b"\xff\xd8\xff\x00", "image/jpeg"),
        ("scan.png", b"\x89PNG\r\n\x1a\nrest", "image/png"),
        ("notes.txt", b"archive text", "text/plain"),
        ("table.csv", b"year,title\n2025,Report\n", "text/csv"),
    ],
)
def test_upload_file_type_validation(filename, content, expected_type):
    safe_name, media_type, extension = main_module.validate_uploaded_file(filename, content)

    assert safe_name == filename
    assert media_type == expected_type
    assert extension == filename.rsplit(".", 1)[1].lower()


def test_document_files_are_attached_to_geojson_documents():
    store = FakeAuthStore("catalog-admin", "unused")
    store.files = {
        1: {
            "id": 1,
            "document_id": 17,
            "original_filename": "report.pdf",
            "size_bytes": 1234,
        },
        2: {
            "id": 2,
            "document_id": 17,
            "original_filename": "notes.txt",
            "size_bytes": 42,
        },
    }
    row = {"documents": [{"id": 17, "title": "Survey"}]}

    main_module.attach_document_files(FakeConnection(store), [row])

    assert row["documents"][0]["files"] == [
        {"id": 2, "filename": "notes.txt", "size_bytes": 42},
        {"id": 1, "filename": "report.pdf", "size_bytes": 1234},
    ]


def test_feature_detail_includes_downloadable_document_metadata(auth_client):
    auth_client.test_store.geo_feature_row = {
        "id": 7,
        "name": "Existing site",
        "kind": "well",
        "metadata": {},
        "geometry": {"type": "Point", "coordinates": [150.8, 59.56]},
        "source_geometry": {"type": "Point", "coordinates": [150.8, 59.56]},
        "source_crs": "EPSG:4326",
        "documents": [{"id": 17, "title": "Survey"}],
    }
    auth_client.test_store.files[1] = {
        "id": 1,
        "document_id": 17,
        "original_filename": "report.pdf",
        "size_bytes": 1234,
    }
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )

    response = auth_client.get("/api/features/7")

    assert response.status_code == 200
    assert response.json()["properties"]["documents"][0]["files"] == [
        {"id": 1, "filename": "report.pdf", "size_bytes": 1234}
    ]


def test_gsk_feature_detail_keeps_source_geometry_but_has_no_globe_geometry(auth_client):
    auth_client.test_store.geo_feature_row = {
        "id": 8,
        "name": "GSK site",
        "kind": "well",
        "metadata": {},
        "geometry": None,
        "source_geometry": {"type": "Point", "coordinates": [150.8, 59.56]},
        "source_crs": "EPSG:7683",
        "documents": [],
    }
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )

    response = auth_client.get("/api/features/8")

    assert response.status_code == 200
    assert response.json()["geometry"] is None
    assert response.json()["properties"]["source_crs"] == "EPSG:7683"
    assert response.json()["properties"]["source_geometry"]["coordinates"] == [150.8, 59.56]


def test_csv_import_preview_validates_gsk_without_writing(auth_client):
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    body = (
        "feature_name,geometry_kind,coordinate_crs,document_title,longitude,latitude,authors\n"
        "GSK site,well,EPSG:7683,Survey,150.8,59.56,Geologist\n"
    )

    response = auth_client.post(
        "/api/admin/import/csv/preview",
        content=body.encode(),
        headers={"Origin": "https://catalog.test", "Content-Type": "text/csv"},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["rows"][0]["coordinate_crs"] == "EPSG:7683"
    assert not auth_client.test_store.features
    assert not auth_client.test_store.documents


def test_csv_import_preview_rejects_out_of_range_coordinates(auth_client):
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    body = (
        "feature_name,geometry_kind,coordinate_crs,document_title,longitude,latitude\n"
        "Broken,well,EPSG:7683,Survey,181,59\n"
    )

    response = auth_client.post(
        "/api/admin/import/csv/preview",
        content=body.encode(),
        headers={"Origin": "https://catalog.test", "Content-Type": "text/csv"},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "outside longitude/latitude ranges" in response.json()["rows"][0]["errors"][0]


def test_csv_import_commit_is_repeat_safe_and_preserves_gsk(auth_client):
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    body = (
        "feature_name,geometry_kind,coordinate_crs,document_title,longitude,latitude,tgf_number\n"
        "GSK site,well,EPSG:7683,Survey,150.8,59.56,TGF-0042\n"
    )
    headers = {"Origin": "https://catalog.test", "Content-Type": "text/csv"}

    imported = auth_client.post("/api/admin/import/csv/commit", content=body.encode(), headers=headers)
    repeated = auth_client.post("/api/admin/import/csv/commit", content=body.encode(), headers=headers)

    assert imported.status_code == 201
    assert imported.json()["imported_documents"] == 1
    assert repeated.status_code == 201
    assert repeated.json()["imported_documents"] == 0
    assert repeated.json()["skipped_repeats"] == 1
    assert auth_client.test_store.features[1]["source_crs"] == "EPSG:7683"
    assert len(auth_client.test_store.documents) == 1
    assert auth_client.test_store.documents[1]["tgf_number"] == "TGF-0042"
    assert len(auth_client.test_store.feature_documents) == 1


def test_xlsx_import_preview_and_rejects_formulas():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["feature_name", "geometry_kind", "coordinate_crs", "document_title", "source_wkt"])
    sheet.append(["GSK site", "well", "EPSG:7683", "Survey", "POINT(150.8 59.56)"])
    output = BytesIO()
    workbook.save(output)

    rows = main_module.parse_catalog_xlsx(output.getvalue())

    assert rows[0]["errors"] == []
    assert rows[0]["wkt"] == "POINT(150.8 59.56)"

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["feature_name", "geometry_kind", "coordinate_crs", "document_title"])
    sheet.append(["=1+1", "well", "EPSG:7683", "Survey"])
    output = BytesIO()
    workbook.save(output)
    with pytest.raises(HTTPException) as error:
        main_module.parse_catalog_xlsx(output.getvalue())
    assert "formulas are not allowed" in error.value.detail


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("script.exe", b"MZ"),
        ("fake.pdf", b"<html>not a pdf</html>"),
        ("fake.docx", b"PK but not an office zip"),
        ("bad.txt", b"\x00binary"),
    ],
)
def test_upload_rejects_unsupported_or_mismatched_file(filename, content):
    with pytest.raises(HTTPException) as error:
        main_module.validate_uploaded_file(filename, content)
    assert error.value.status_code == 415


def test_polygon_vertices_are_checked_and_closed():
    vertices = main_module.parse_polygon_vertices("150.8,59.5;151.2,59.5;151.2,60")

    assert len(vertices) == 4
    assert vertices[0] == vertices[-1]
    with pytest.raises(HTTPException):
        main_module.parse_polygon_vertices("150.8,59.5; 151.2,95; 151.2,60")


def test_magadan_demo_migration_adds_two_document_linked_points():
    migration = Path(__file__).parents[1] / "sql/003_magadan_demo_points.sql"
    sql = migration.read_text(encoding="utf-8")

    assert sql.count("'well'") == 2
    assert "DEMO-MAGADAN-0001" in sql
    assert "DEMO-MAGADAN-0002" in sql
    assert "150.80, 59.56" in sql
    assert "153.20, 60.80" in sql
    assert "ON CONFLICT (inventory_number) DO UPDATE" in sql
    assert "ON CONFLICT (name) DO UPDATE" in sql
    assert "ON CONFLICT DO NOTHING" in sql


def test_bootstrap_admin_migration_trims_secret_files(monkeypatch):
    password_hash = PasswordHasher().hash("a-strong-test-password")
    monkeypatch.setenv("AUTH_USERNAME", "catalog-admin\n")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", password_hash + "\n")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-that-is-at-least-32-bytes\n")
    store = FakeAuthStore.__new__(FakeAuthStore)
    store.users = {}
    store.next_id = 1
    monkeypatch.setattr(main_module, "get_connection", store.connect)

    main_module.ensure_users_table()

    assert store.users[1]["username"] == "catalog-admin"
    assert store.users[1]["role"] == "admin"
    assert PasswordHasher().verify(store.users[1]["password_hash"], "a-strong-test-password")
    store.users[1]["password_hash"] = PasswordHasher().hash("changed-admin-password")
    main_module.ensure_users_table()
    assert PasswordHasher().verify(store.users[1]["password_hash"], "changed-admin-password")


def test_catalog_requires_authentication(auth_client):
    page = auth_client.get("/", follow_redirects=False)
    api = auth_client.get("/api/features")
    assert page.status_code == 303
    assert page.headers["cache-control"] == "no-store"
    assert "content-security-policy" in page.headers
    assert api.status_code == 401
    assert api.headers["cache-control"] == "no-store"
    assert auth_client.get("/static/app.js", follow_redirects=False).status_code == 303
    assert auth_client.get("/static/login.css").status_code == 200
    assert auth_client.get("/static/ne_110m_admin_0_countries.geojson").status_code == 200


def test_login_sets_secure_http_only_session(auth_client):
    response = auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert auth_client.get("/", follow_redirects=False).status_code == 200


def test_login_rejects_bad_password(auth_client):
    response = auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=invalid" in response.headers["location"]


def test_admin_can_create_and_manage_a_viewer(auth_client):
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    created = auth_client.post(
        "/api/admin/users",
        json={"username": "colleague", "password": "colleague-pass-2026", "role": "viewer"},
    )
    assert created.status_code == 201
    assert created.json()["role"] == "viewer"

    user = auth_client.test_store.users[created.json()["id"]]
    assert PasswordHasher().verify(user["password_hash"], "colleague-pass-2026")
    assert "password_hash" not in created.json()

    auth_client.cookies.clear()
    login = auth_client.post(
        "/auth/login",
        data={"username": "colleague", "password": "colleague-pass-2026"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert auth_client.get("/api/session").json()["role"] == "viewer"
    assert auth_client.get("/api/admin/users").status_code == 403
    assert auth_client.get("/admin/users", follow_redirects=False).status_code == 303


def test_admin_uploads_files_with_a_new_point_and_downloads_them(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "upload_root", tmp_path)
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    response = auth_client.post(
        "/api/admin/documents",
        data={
            "feature_mode": "new",
            "feature_name": "North borehole",
            "geometry_kind": "well",
            "longitude": "150.8",
            "latitude": "59.56",
            "feature_metadata": "{}",
            "document_title": "Survey report",
            "inventory_number": "INV-001",
            "tgf_number": "TGF-001",
            "region": "Magadan",
            "document_year": "2025",
        },
        files=[
            ("files", ("survey.pdf", b"%PDF-1.7 test content", "application/pdf")),
            ("files", ("notes.txt", b"field notes", "text/plain")),
        ],
        headers={"Origin": "https://catalog.test"},
    )

    assert response.status_code == 201
    assert len(response.json()["files"]) == 2
    assert len(auth_client.test_store.features) == 1
    assert auth_client.test_store.features[1]["source_crs"] == "EPSG:7683"
    assert auth_client.test_store.features[1]["geom"] is None
    assert len(auth_client.test_store.documents) == 1
    assert auth_client.test_store.documents[1]["tgf_number"] == "TGF-001"
    assert len(auth_client.test_store.files) == 2

    file_id, file_info = next(iter(auth_client.test_store.files.items()))
    downloaded = auth_client.get(f"/api/files/{file_id}")
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-1.7 test content"
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert "attachment" in downloaded.headers["content-disposition"]
    assert (tmp_path / file_info["storage_key"]).exists()


def test_admin_can_add_a_document_to_an_existing_object(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "upload_root", tmp_path)
    auth_client.test_store.features[7] = {"id": 7, "name": "Existing site"}
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    response = auth_client.post(
        "/api/admin/documents",
        data={
            "feature_mode": "existing",
            "feature_id": "7",
            "document_title": "Existing-site report",
        },
        files={"files": ("report.pdf", b"%PDF-1.7 test", "application/pdf")},
        headers={"Origin": "https://catalog.test"},
    )

    assert response.status_code == 201
    assert response.json()["feature_id"] == 7
    assert list(auth_client.test_store.features) == [7]
    assert len(auth_client.test_store.documents) == 1


def test_admin_can_create_area_with_document(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "upload_root", tmp_path)
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    response = auth_client.post(
        "/api/admin/documents",
        data={
            "feature_mode": "new",
            "feature_name": "Survey area",
            "geometry_kind": "area",
            "polygon_vertices": "150.8,59.5;151.2,59.5;151.2,60",
            "document_title": "Area report",
        },
        files={"files": ("report.pdf", b"%PDF-1.7 test", "application/pdf")},
        headers={"Origin": "https://catalog.test"},
    )

    assert response.status_code == 201
    assert auth_client.test_store.features[1]["name"] == "Survey area"
    assert len(auth_client.test_store.documents) == 1


def test_upload_database_failure_removes_written_files(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "upload_root", tmp_path)
    auth_client.test_store.features[7] = {"id": 7, "name": "Taken name"}
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    response = auth_client.post(
        "/api/admin/documents",
        data={
            "feature_mode": "new",
            "feature_name": "Taken name",
            "geometry_kind": "well",
            "longitude": "150.8",
            "latitude": "59.56",
            "document_title": "Conflicting report",
        },
        files={"files": ("report.pdf", b"%PDF-1.7 test", "application/pdf")},
        headers={"Origin": "https://catalog.test"},
    )

    assert response.status_code == 409
    assert list(tmp_path.iterdir()) == []
    assert not auth_client.test_store.documents


def test_viewer_cannot_upload_files(auth_client):
    auth_client.test_store.users[2] = {
        "id": 2,
        "username": "viewer",
        "password_hash": PasswordHasher().hash("viewer-password-2026"),
        "role": "viewer",
        "is_active": True,
        "token_version": 0,
        "created_at": datetime.now(timezone.utc),
    }
    auth_client.post(
        "/auth/login",
        data={"username": "viewer", "password": "viewer-password-2026"},
        follow_redirects=False,
    )
    response = auth_client.post(
        "/api/admin/documents",
        files={"files": ("report.pdf", b"%PDF-1.7 test", "application/pdf")},
        headers={"Origin": "https://catalog.test"},
    )

    assert response.status_code == 403


def test_viewer_can_download_uploaded_file(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "upload_root", tmp_path)
    stored_name = "b" * 48 + ".pdf"
    (tmp_path / stored_name).write_bytes(b"%PDF-1.7 test")
    auth_client.test_store.files[1] = {
        "id": 1,
        "document_id": 1,
        "storage_key": stored_name,
        "original_filename": "report.pdf",
        "media_type": "application/pdf",
        "size_bytes": 12,
    }
    auth_client.test_store.users[2] = {
        "id": 2,
        "username": "viewer",
        "password_hash": PasswordHasher().hash("viewer-password-2026"),
        "role": "viewer",
        "is_active": True,
        "token_version": 0,
        "created_at": datetime.now(timezone.utc),
    }
    auth_client.post(
        "/auth/login",
        data={"username": "viewer", "password": "viewer-password-2026"},
        follow_redirects=False,
    )

    response = auth_client.get("/api/files/1")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.7 test"


def test_admin_can_delete_uploaded_file(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "upload_root", tmp_path)
    stored_name = "a" * 48 + ".pdf"
    stored_path = tmp_path / stored_name
    stored_path.write_bytes(b"%PDF-1.7 test")
    auth_client.test_store.files[1] = {
        "id": 1,
        "document_id": 1,
        "storage_key": stored_name,
        "original_filename": "report.pdf",
        "media_type": "application/pdf",
        "size_bytes": 12,
    }
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )

    deleted = auth_client.delete(
        "/api/admin/files/1",
        headers={"Origin": "https://catalog.test"},
    )

    assert deleted.status_code == 204
    assert not stored_path.exists()


def test_admin_can_reset_password_and_revoke_existing_session(auth_client):
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    created = auth_client.post(
        "/api/admin/users",
        json={"username": "colleague", "password": "colleague-pass-2026", "role": "viewer"},
    ).json()

    auth_client.cookies.clear()
    auth_client.post(
        "/auth/login",
        data={"username": "colleague", "password": "colleague-pass-2026"},
        follow_redirects=False,
    )
    assert auth_client.get("/api/session").status_code == 200
    old_session = auth_client.cookies.get("geocatalog_session")

    auth_client.cookies.clear()
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    reset = auth_client.patch(
        f"/api/admin/users/{created['id']}",
        json={"password": "new-colleague-pass-2026"},
    )
    assert reset.status_code == 200

    auth_client.cookies.clear()
    auth_client.cookies.set("geocatalog_session", old_session)
    assert auth_client.get("/api/session").status_code == 401
    login = auth_client.post(
        "/auth/login",
        data={"username": "colleague", "password": "new-colleague-pass-2026"},
        follow_redirects=False,
    )
    assert login.status_code == 303


def test_last_admin_cannot_be_deactivated(auth_client):
    auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )
    response = auth_client.patch("/api/admin/users/1", json={"active": False})
    assert response.status_code == 409


def test_login_accepts_secret_values_with_trailing_newlines(auth_client, monkeypatch):
    monkeypatch.setenv("AUTH_USERNAME", "catalog-admin\n")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", os.environ["AUTH_PASSWORD_HASH"] + "\n")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-that-is-at-least-32-bytes\n")

    response = auth_client.post(
        "/auth/login",
        data={"username": "catalog-admin", "password": "a-strong-test-password"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_refuses_plain_http(monkeypatch):
    monkeypatch.setenv("AUTH_USERNAME", "catalog-admin")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", PasswordHasher().hash("a-strong-test-password"))
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-that-is-at-least-32-bytes")
    monkeypatch.setattr(main_module, "ensure_users_table", lambda: None)
    with TestClient(app, base_url="http://catalog.test") as client:
        response = client.post(
            "/auth/login",
            data={"username": "catalog-admin", "password": "anything"},
        )
    assert response.status_code == 426


def test_auth_cli_accepts_eight_character_password(tmp_path, monkeypatch):
    import sys

    prompts = iter(["admin", "12345678", "12345678"])
    monkeypatch.setattr("builtins.input", lambda _: next(prompts))
    monkeypatch.setattr(auth_cli.getpass, "getpass", lambda _: next(prompts))
    monkeypatch.setattr(sys, "argv", ["auth_cli", "--output-dir", str(tmp_path / "auth")])

    auth_cli.main()

    from argon2 import PasswordHasher

    password_hash = (tmp_path / "auth" / "AUTH_PASSWORD_HASH").read_text().strip()
    assert PasswordHasher().verify(password_hash, "12345678")


def test_auth_cli_rejects_password_shorter_than_eight(tmp_path, monkeypatch):
    import sys

    prompts = iter(["admin", "1234567", "1234567"])
    monkeypatch.setattr("builtins.input", lambda _: next(prompts))
    monkeypatch.setattr(auth_cli.getpass, "getpass", lambda _: next(prompts))
    monkeypatch.setattr(sys, "argv", ["auth_cli", "--output-dir", str(tmp_path / "auth")])

    with pytest.raises(SystemExit):
        auth_cli.main()


@pytest.mark.parametrize(
    "value",
    ["a,b,c,d", "181,0,190,10", "0,91,10,95", "40,50,30,60", "30,60,40,50", "nan,0,10,10"],
)
def test_invalid_bbox(value):
    with pytest.raises(HTTPException) as error:
        parse_bbox(value)
    assert error.value.status_code == 422
