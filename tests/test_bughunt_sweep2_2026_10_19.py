"""Bug-hunt sweep #2 (second pass) regression tests — 2026-10-19.

Findings covered, all in the hostile/corrupt-hub + identity-lifecycle
family the earlier passes kept finding one instance at a time:

  * A hub token only had to be a non-empty string. A token containing
    CR/LF, a space, or a character requests cannot put in a header
    makes EVERY authenticated POST raise InvalidHeader — the worker
    wedged on "heartbeat failed" forever instead of offering
    re-pairing. Tokens are urlsafe by contract (the hub mints them
    with secrets.token_urlsafe), so register() and load_identity()
    now enforce that alphabet.
  * load_identity()'s hub_base check stopped at the scheme prefix:
    "http://" (no host), an out-of-range port, or embedded whitespace
    all passed and wedged the worker the same way. The stored base
    must now parse as a usable http(s) URL.
  * delete_identity() left a crash-orphaned helper.json.tmp behind —
    that file holds a copy of the token, so "unpaired" was not true.
  * save_identity() chmodded the identity file only AFTER replacing
    it; the tmp file holding the token sat at umask permissions in
    between (and forever, after a crash in the window).
  * HelperWorker could never be restarted: stop() set the stop event
    and start() never cleared it, so a second start() ran a thread
    that registered and immediately exited without heartbeating.
  * Hub error strings were interpolated into status/dialog text
    unbounded and untyped; they are now string-checked and capped
    at 500 chars (the cap worker.py already used for result errors),
    and a non-string hub_id is no longer persisted verbatim.
"""

import json
import os
import stat

import pytest

import helper.registration as registration
import helper.worker as worker_mod
from helper.registration import valid_hub_base, valid_token

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


class _FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ------------------------------------------------------------------- tokens
@pytest.mark.parametrize(
    "bad_token",
    [
        "bad\r\nInjected: 1",  # header injection — InvalidHeader on every POST
        "bad token",  # space is not a legal header/token char here
        "tökén☃",  # characters requests cannot encode in a header value
        "tok\ten",
        "a/b",  # not in the urlsafe alphabet the hub mints
        "a" * 513,  # unbounded bearer string
        "",
    ],
)
def test_load_identity_rejects_unusable_tokens(tmp_path, monkeypatch, bad_token):
    _write_identity(tmp_path, monkeypatch, {**IDENTITY, "token": bad_token})
    assert registration.load_identity() is None


def test_valid_token_accepts_hub_minted_shape():
    # secrets.token_urlsafe(32): 43 chars over [A-Za-z0-9_-].
    assert valid_token("aB3-_xY9" * 5 + "abc")
    assert valid_token("test-token")
    assert valid_token("abc=")  # tolerate padded urlsafe encodings
    assert not valid_token(None)
    assert not valid_token(123)


@pytest.mark.parametrize("bad_token", ["bad\r\nInjected: 1", "has space", "☃"])
def test_register_rejects_unusable_hub_token(monkeypatch, tmp_path, bad_token):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        registration.requests,
        "post",
        lambda *a, **k: _FakeResp(200, {"ok": True, "token": bad_token}),
    )
    with pytest.raises(RuntimeError, match="token"):
        registration.register("http://hub.invalid:9999", "Name", {})
    assert registration.load_identity() is None


# ----------------------------------------------------------------- hub_base
@pytest.mark.parametrize(
    "bad_base",
    [
        "http://",  # scheme but no host at all
        "http://hub:99999999",  # port out of range
        "http://hub name:9999",  # whitespace in the host
        "http://hub\t:9999",
        "http://[::1",  # unterminated IPv6 literal
        "https://",
    ],
)
def test_load_identity_rejects_unusable_hub_base(tmp_path, monkeypatch, bad_base):
    _write_identity(tmp_path, monkeypatch, {**IDENTITY, "hub_base": bad_base})
    assert registration.load_identity() is None


@pytest.mark.parametrize(
    "good_base",
    [
        "http://hub.invalid:9999",
        "https://hub.example.ts.net",
        "http://100.64.1.2:8765",
        "http://[::1]:9999",
        "http://hub.invalid:9999/prefix",
    ],
)
def test_valid_hub_base_accepts_real_shapes(good_base):
    assert valid_hub_base(good_base)


# ------------------------------------------------------- identity lifecycle
def test_delete_identity_also_removes_tmp_leftover(tmp_path, monkeypatch):
    p = _write_identity(tmp_path, monkeypatch, IDENTITY)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(IDENTITY), encoding="utf-8")  # crash orphan
    registration.delete_identity()
    assert not p.exists()
    assert not tmp.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_save_identity_restricts_tmp_before_replace(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    modes = {}
    real_replace = os.replace

    def spy_replace(src, dst):
        modes["at_replace"] = stat.S_IMODE(os.stat(src).st_mode)
        return real_replace(src, dst)

    monkeypatch.setattr(registration.os, "replace", spy_replace)
    registration.save_identity(dict(IDENTITY))
    assert modes["at_replace"] == 0o600


# ------------------------------------------------------------ hub responses
def test_register_drops_non_string_hub_id(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        registration.requests,
        "post",
        lambda *a, **k: _FakeResp(
            200, {"ok": True, "token": "tok-123", "hub_id": {"evil": 1}}
        ),
    )
    ident = registration.register("http://hub.invalid:9999", "Name", {})
    assert ident["hub_id"] == ""


def test_hub_error_string_is_capped():
    resp = _FakeResp(400, {"error": "x" * 5000})
    assert len(registration._hub_error(resp, "dflt")) == 500
    resp = _FakeResp(400, {"error": {"not": "a string"}})
    assert registration._hub_error(resp, "dflt") == "dflt"


def test_worker_post_400_caps_and_typechecks_hub_error(monkeypatch):
    statuses = []
    w = worker_mod.HelperWorker(on_status=statuses.append)
    monkeypatch.setattr(
        worker_mod.requests,
        "post",
        lambda *a, **k: _FakeResp(400, {"error": "y" * 5000}),
    )
    assert w._post(IDENTITY, "/helper/v1/heartbeat", {}) is None
    assert statuses == ["protocol mismatch: " + "y" * 500]

    statuses.clear()
    monkeypatch.setattr(
        worker_mod.requests,
        "post",
        lambda *a, **k: _FakeResp(400, {"error": ["not", "a", "string"]}),
    )
    assert w._post(IDENTITY, "/helper/v1/heartbeat", {}) is None
    assert statuses == ["protocol mismatch: protocol mismatch"]


# ------------------------------------------------------------ worker restart
def test_worker_can_restart_after_stop(monkeypatch):
    monkeypatch.setattr(worker_mod, "load_identity", lambda: dict(IDENTITY))
    calls = []
    w = worker_mod.HelperWorker(on_status=lambda _s: None)
    w._system_idle = lambda: False

    def fake_post(identity, path, body, timeout=30):
        calls.append(path)
        w._stop_event.set()  # end this run after one heartbeat
        return {"ok": True}

    w._post = fake_post
    w.start()
    w._thread.join(timeout=5)
    assert not w._thread.is_alive()
    assert len(calls) == 1

    # Second start on the SAME worker must actually run the loop again
    # (before the fix the stop event stayed set and the thread exited
    # at the loop condition without a single heartbeat).
    w.start()
    w._thread.join(timeout=5)
    assert not w._thread.is_alive()
    assert len(calls) == 2
