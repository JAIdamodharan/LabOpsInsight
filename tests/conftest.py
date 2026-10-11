"""Existing (pre-auth) tests exercise the app with authentication off; auth tests enable it explicitly with create_app(auth=True)."""
import pytest


@pytest.fixture(autouse=True)
def _auth_off_by_default(monkeypatch):
    monkeypatch.setenv("LABOPS_AUTH", "0")


@pytest.fixture(autouse=True)
def _cheap_scrypt(monkeypatch):
    from analyst import auth
    monkeypatch.setattr(auth, "SCRYPT_N", 2 ** 4)       # tests only: real runs use 2**14
