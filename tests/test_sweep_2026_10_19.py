"""All-repo sweep regression tests — 2026-10-19.

Covers three findings in the corrupt-identity / broken-hub family the
earlier passes fixed one instance at a time:

  * protocol.valid_body_id used re.match with a "$" anchor, which also
    matches just before a trailing newline — "helper-<12 hex>\n" was
    accepted as a valid id.
  * registration.register() only checked the hub's token was truthy:
    a non-string token (123, {...}) was persisted, then wedged the
    session — _headers() TypeErrors on "Bearer " + token — and was
    rejected by load_identity on the next launch anyway.
  * registration.load_identity accepted any non-empty string hub_base:
    a scheme requests cannot speak spins the worker on "heartbeat
    failed" forever instead of offering re-pairing (the JH3 wedge),
    even though the hub is HTTP(S) by contract.
"""

import json

import pytest

import helper.registration as registration
from helper.protocol import valid_body_id

IDENTITY = {
    "body_id": "helper-abcdef012345",
    "token": "test-token",
    "hub_base": "http://hub.invalid:9999",
    "name": "Test Helper",
}


def _write_identity(tmp_path, monkeypatch, data):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    p = tmp_path / "CyraHelper" / "helper.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ------------------------------------------------------------------ protocol
def test_valid_body_id_rejects_trailing_newline():
    assert valid_body_id("helper-abcdef012345") is True
    assert valid_body_id("helper-abcdef012345\n") is False
    assert valid_body_id("helper-abcdef012345\r\n") is False
    assert valid_body_id("helper-abcdef01234") is False
    assert valid_body_id("helper-ABCDEF012345") is False
    assert valid_body_id(None) is False


# --------------------------------------------------------------- registration
class _FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.mark.parametrize("bad_token", [123, 12.5, {"t": 1}, ["t"], True])
def test_register_non_string_token_raises_clear_error(
    monkeypatch, tmp_path, bad_token
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        registration.requests,
        "post",
        lambda *a, **k: _FakeResp(200, {"ok": True, "token": bad_token}),
    )
    with pytest.raises(RuntimeError, match="token"):
        registration.register("http://hub.invalid:9999", "Name", {})
    # Nothing may be persisted for a poisoned registration.
    assert registration.load_identity() is None


def test_register_string_token_still_works(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        registration.requests,
        "post",
        lambda *a, **k: _FakeResp(200, {"ok": True, "token": "tok-123"}),
    )
    ident = registration.register("http://hub.invalid:9999", "Name", {})
    assert ident["token"] == "tok-123"
    assert registration.load_identity()["token"] == "tok-123"


@pytest.mark.parametrize(
    "bad_base", ["hub.invalid:9999", "ftp://hub.invalid", "//hub.invalid", ""]
)
def test_load_identity_rejects_non_http_hub_base(tmp_path, monkeypatch, bad_base):
    _write_identity(tmp_path, monkeypatch, {**IDENTITY, "hub_base": bad_base})
    assert registration.load_identity() is None


def test_load_identity_accepts_https_hub_base(tmp_path, monkeypatch):
    _write_identity(
        tmp_path, monkeypatch, {**IDENTITY, "hub_base": "https://hub.invalid:9999"}
    )
    assert registration.load_identity()["hub_base"] == "https://hub.invalid:9999"
