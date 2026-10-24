"""Sweep 5 regression tests — 2026-10-24.

Finding: helper.worker._post let a bare ValueError escape when a 200
response body wasn't JSON — only the JSON parser's message
("Expecting value: line 1 column 1 (char 0)") reached the volunteer's
status line via the heartbeat loop. registration.register() got this
exact fix in BH2 ("hub registration response was not JSON"); the
authenticated _post path (heartbeat / job fetch / job result) never
did. Demonstrated on the pre-fix code with a non-JSON 200 fake.

Fix: _post raises a clear RuntimeError("hub response was not JSON: …").
Behavior pins: a valid JSON 200 still returns its dict, and the
400/401/403 terminal paths still return None.
"""

import helper.worker as worker_mod
from helper.worker import HelperWorker

IDENTITY = {
    "body_id": "helper-abcdef012345",
    "token": "test-token",
    "hub_base": "http://hub.invalid:9999",
    "name": "Test Helper",
}


class _FakeResp:
    def __init__(self, status, payload=None, json_raises=False):
        self.status_code = status
        self._payload = payload
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _worker_with_resp(monkeypatch, resp):
    monkeypatch.setattr(worker_mod.requests, "post", lambda *a, **k: resp)
    return HelperWorker()


def test_post_non_json_200_raises_clear_error(monkeypatch):
    w = _worker_with_resp(monkeypatch, _FakeResp(200, json_raises=True))
    try:
        w._post(IDENTITY, "/helper/v1/heartbeat", {})
    except RuntimeError as e:
        assert "not JSON" in str(e)
    except ValueError:
        raise AssertionError("bare ValueError escaped _post (pre-fix behavior)")
    else:
        raise AssertionError("_post accepted a non-JSON 200 body")


def test_post_valid_json_200_still_returns_dict(monkeypatch):
    w = _worker_with_resp(monkeypatch, _FakeResp(200, payload={"ok": True}))
    assert w._post(IDENTITY, "/helper/v1/heartbeat", {}) == {"ok": True}


def test_post_400_still_returns_none(monkeypatch):
    statuses = []
    w = _worker_with_resp(
        monkeypatch, _FakeResp(400, payload={"error": "protocol mismatch"}))
    w.on_status = statuses.append
    assert w._post(IDENTITY, "/helper/v1/heartbeat", {}) is None
    assert statuses and "protocol mismatch" in statuses[0]
