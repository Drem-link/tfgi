import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app, livez, parse_bbox


@pytest.fixture
def auth_client(monkeypatch):
    from argon2 import PasswordHasher

    monkeypatch.setenv("AUTH_USERNAME", "catalog-admin")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", PasswordHasher().hash("a-strong-test-password"))
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-that-is-at-least-32-bytes")
    return TestClient(app, base_url="https://catalog.test")


def test_parse_bbox():
    assert parse_bbox("30,50,40,60") == (30.0, 50.0, 40.0, 60.0)


def test_livez_does_not_require_database():
    assert livez() == {"status": "ok"}


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


def test_login_accepts_unicode_credentials(monkeypatch):
    from argon2 import PasswordHasher

    monkeypatch.setenv("AUTH_USERNAME", "архив")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", PasswordHasher().hash("геология-пароль-2026"))
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-that-is-at-least-32-bytes")
    with TestClient(app, base_url="https://catalog.test") as client:
        response = client.post(
            "/auth/login",
            data={"username": "архив", "password": "геология-пароль-2026"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert client.get("/", follow_redirects=False).status_code == 200


def test_login_refuses_plain_http():
    with TestClient(app, base_url="http://catalog.test") as client:
        response = client.post(
            "/auth/login",
            data={"username": "catalog-admin", "password": "anything"},
        )
    assert response.status_code == 426


@pytest.mark.parametrize(
    "value",
    ["a,b,c,d", "181,0,190,10", "0,91,10,95", "40,50,30,60", "30,60,40,50", "nan,0,10,10"],
)
def test_invalid_bbox(value):
    with pytest.raises(HTTPException) as error:
        parse_bbox(value)
    assert error.value.status_code == 422
