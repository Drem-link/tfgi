import os
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from argon2 import PasswordHasher
from psycopg.errors import UniqueViolation

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
        if sql.startswith("create table") or "pg_advisory_xact_lock" in sql:
            return FakeResult([])
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
