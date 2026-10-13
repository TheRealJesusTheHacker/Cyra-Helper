"""Hardening regression tests — security audit + bug hunt, 2026-10-13.

Covers the JH3 findings:
  * render: hub-supplied model ids with "." / ".." path segments must be
    rejected (the owner/repo regex alone lets them through, and
    from_pretrained treats an existing relative path as a local dir).
  * render: the per-model consent gate still refuses unapproved models
    and remembers approvals.
  * registration: a stored identity without hub_base is corrupt — it must
    load as "no identity", not wedge the worker on KeyError forever.
  * registration: register() must surface the hub's default error (not a
    bare ValueError/KeyError) on non-JSON error bodies / token-less 200s.
  * worker: malformed hub payloads (non-dict response, non-dict job,
    non-dict params) must not raise out of _fetch_and_run — before JH3
    they killed the daemon worker thread silently.
  * worker: busy heartbeats keep flowing while a render runs, and a stop
    request mid-render returns promptly instead of blocking on the GPU.
"""

import importlib
import json
import time

import pytest

# importlib, not "import helper.jobs.render as …": helper.jobs.__init__
# re-exports the render *function* under the same attribute name.
render_mod = importlib.import_module("helper.jobs.render")
import helper.registration as registration
import helper.worker as worker_mod
from helper.worker import HelperWorker

IDENTITY = {
    "body_id": "helper-abcdef012345",
    "token": "test-token",
    "hub_base": "http://hub.invalid:9999",
    "name": "Test Helper",
}


# --------------------------------------------------------------------- render
class _FakeImage:
    def save(self, buf, format=None, **_kwargs):
        buf.write(b"\x89PNG-fake")


class _FakePipe:
    def __call__(self, **kwargs):
        class _Out:
            images = [_FakeImage()]

        return _Out()


def test_render_rejects_dot_segment_model_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(
        render_mod, "_load_pipeline", lambda m: calls.append(m) or _FakePipe()
    )
    for bad in ("../foo", "foo/..", "./foo", "foo/.", "..%2Ffoo"):
        with pytest.raises(ValueError):
            render_mod.render({"prompt": "a dog", "model": bad})
    assert calls == []  # validation happens before any pipeline load


def test_render_rejects_non_repo_model_ids(monkeypatch):
    monkeypatch.setattr(
        render_mod, "_load_pipeline", lambda m: _FakePipe()
    )
    for bad in ("/abs/path", "C:\\models\\x", "a/b/c", "noslash", "a//b"):
        with pytest.raises(ValueError):
            render_mod.render({"prompt": "a dog", "model": bad})


def test_render_happy_path_with_fake_pipeline(monkeypatch):
    monkeypatch.setattr(render_mod, "_load_pipeline", lambda m: _FakePipe())
    png = render_mod.render({"prompt": "a dog", "width": 500, "height": 500})
    assert png == b"\x89PNG-fake"


def test_render_png_uses_fast_compression(monkeypatch):
    # Perf pass 2026-10-13: PNG is lossless at any compress_level, so
    # render() encodes at level 1 (~5x faster than PIL's default 6 for
    # ~4% more bytes; decoded pixels identical). Pin the kwarg so the
    # per-job overhead doesn't creep back.
    seen = {}

    class _RecordingImage:
        def save(self, buf, format=None, **kwargs):
            seen["format"] = format
            seen.update(kwargs)
            buf.write(b"\x89PNG-fake")

    class _Pipe:
        def __call__(self, **kwargs):
            class _Out:
                images = [_RecordingImage()]

            return _Out()

    monkeypatch.setattr(render_mod, "_load_pipeline", lambda m: _Pipe())
    png = render_mod.render({"prompt": "a dog"})
    assert png == b"\x89PNG-fake"
    assert seen["format"] == "PNG"
    assert seen["compress_level"] == 1


def test_render_clamps_dimensions(monkeypatch):
    seen = {}

    class _Pipe:
        def __call__(self, **kwargs):
            seen.update(kwargs)

            class _Out:
                images = [_FakeImage()]

            return _Out()

    monkeypatch.setattr(render_mod, "_load_pipeline", lambda m: _Pipe())
    render_mod.render({"prompt": "x", "width": 99999, "height": 1, "steps": 9999})
    assert seen["width"] == 2048
    assert seen["height"] == 64
    assert seen["num_inference_steps"] == 150


def test_consent_gate_refuses_unapproved_model(monkeypatch):
    monkeypatch.setattr(render_mod, "allowed_models", {render_mod.DEFAULT_MODEL})
    monkeypatch.setattr(render_mod, "consent_callback", None)
    monkeypatch.setattr(render_mod, "_load_known_models", lambda: set())
    with pytest.raises(RuntimeError, match="not approved"):
        render_mod._load_pipeline("someone/some-model")


def test_consent_approval_is_remembered(monkeypatch, tmp_path):
    known_file = tmp_path / "known_models.json"
    monkeypatch.setattr(render_mod, "_known_models_path", lambda: known_file)
    monkeypatch.setattr(render_mod, "allowed_models", set())
    monkeypatch.setattr(render_mod, "consent_callback", lambda m: True)
    assert render_mod._model_approved("someone/some-model") is True
    # Second time: remembered on disk, no callback needed.
    monkeypatch.setattr(render_mod, "consent_callback", None)
    assert render_mod._model_approved("someone/some-model") is True
    assert json.loads(known_file.read_text(encoding="utf-8")) == ["someone/some-model"]


# --------------------------------------------------------------- registration
def _write_identity(tmp_path, monkeypatch, data):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    p = tmp_path / "JadeHelper" / "helper.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_identity_valid(tmp_path, monkeypatch):
    _write_identity(tmp_path, monkeypatch, IDENTITY)
    assert registration.load_identity()["body_id"] == IDENTITY["body_id"]


def test_load_identity_without_hub_base_is_invalid(tmp_path, monkeypatch):
    broken = {k: v for k, v in IDENTITY.items() if k != "hub_base"}
    _write_identity(tmp_path, monkeypatch, broken)
    assert registration.load_identity() is None
    _write_identity(tmp_path, monkeypatch, {**IDENTITY, "hub_base": ""})
    assert registration.load_identity() is None


def test_load_identity_non_dict_json_is_invalid(tmp_path, monkeypatch):
    _write_identity(tmp_path, monkeypatch, ["not", "a", "dict"])
    assert registration.load_identity() is None


class _FakeResp:
    def __init__(self, status, payload=None, json_raises=False):
        self.status_code = status
        self._payload = payload
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("no json here")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_register_non_json_400_raises_protocol_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        registration.requests, "post", lambda *a, **k: _FakeResp(400, json_raises=True)
    )
    with pytest.raises(registration.ProtocolMismatchError, match="protocol mismatch"):
        registration.register("http://hub.invalid:9999", "Name", {})


def test_register_non_json_403_raises_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        registration.requests, "post", lambda *a, **k: _FakeResp(403, json_raises=True)
    )
    with pytest.raises(registration.BlockedError, match="blocked"):
        registration.register("http://hub.invalid:9999", "Name", {})


def test_register_200_without_token_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        registration.requests, "post", lambda *a, **k: _FakeResp(200, {"ok": True})
    )
    with pytest.raises(RuntimeError, match="token"):
        registration.register("http://hub.invalid:9999", "Name", {})


def test_save_identity_is_atomic_no_tmp_left(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    registration.save_identity(IDENTITY)
    files = sorted(p.name for p in (tmp_path / "JadeHelper").iterdir())
    assert files == ["helper.json"]
    assert registration.load_identity()["token"] == "test-token"


# --------------------------------------------------------------------- worker
def test_heartbeat_body_matches_contract():
    # Perf pass 2026-10-13: both heartbeat sites build the payload via
    # HelperWorker._heartbeat_body — pin the contract shape (protocol.py)
    # and the busy/paused pass-through.
    w = HelperWorker()
    body = w._heartbeat_body(IDENTITY, busy=True, paused=False)
    assert set(body) == {
        "protocol_v", "body_id", "cpu_pct", "mem_pct",
        "gpu_free_vram_gb", "busy", "paused",
    }
    assert body["protocol_v"] == worker_mod.PROTOCOL_V
    assert body["body_id"] == IDENTITY["body_id"]
    assert body["busy"] is True and body["paused"] is False
    assert isinstance(body["cpu_pct"], float)
    assert isinstance(body["mem_pct"], float)
    assert isinstance(body["gpu_free_vram_gb"], float)


def _worker_with_fake_post(job_payload, monkeypatch, render_impl=None):
    """A HelperWorker whose _post returns job_payload for job/fetch and
    records every call. render is replaced when render_impl is given."""
    w = HelperWorker()
    calls = []

    def fake_post(identity, path, body, timeout=30):
        calls.append((path, body))
        if path == worker_mod.JOB_FETCH_PATH:
            return job_payload
        return {"ok": True}

    w._post = fake_post
    if render_impl is not None:
        monkeypatch.setattr(worker_mod, "render", render_impl)
    return w, calls


def test_fetch_non_dict_response_does_not_raise(monkeypatch):
    w, _ = _worker_with_fake_post(["not-a-dict"], monkeypatch)
    w._fetch_and_run(IDENTITY)  # must return, not raise


def test_fetch_non_dict_job_does_not_raise(monkeypatch):
    w, _ = _worker_with_fake_post({"job": "junk-job"}, monkeypatch)
    w._fetch_and_run(IDENTITY)


def test_fetch_non_dict_params_are_coerced(monkeypatch):
    seen = {}

    def fake_render(params):
        seen.update(params)
        return b"\x89PNG"

    w, calls = _worker_with_fake_post(
        {"job": {"job_id": "job-1", "type": "media.render", "params": [1, 2, 3]}},
        monkeypatch,
        render_impl=fake_render,
    )
    w._fetch_and_run(IDENTITY)
    assert seen == {}  # non-dict params dropped before render sees them
    results = [b for p, b in calls if p == worker_mod.JOB_RESULT_PATH]
    assert results and results[0]["ok"] is True


def test_heartbeats_continue_during_long_render(monkeypatch):
    monkeypatch.setattr(worker_mod, "RENDER_HEARTBEAT_SECS", 0.2, raising=False)

    def slow_render(params):
        time.sleep(1.0)
        return b"\x89PNG-slow"

    w, calls = _worker_with_fake_post(
        {"job": {"job_id": "job-9", "type": "media.render", "params": {"prompt": "x"}}},
        monkeypatch,
        render_impl=slow_render,
    )
    w._fetch_and_run(IDENTITY)
    busy_hbs = [
        b for p, b in calls if p == worker_mod.HEARTBEAT_PATH and b.get("busy")
    ]
    assert len(busy_hbs) >= 3  # initial ping + pings while rendering
    results = [b for p, b in calls if p == worker_mod.JOB_RESULT_PATH]
    assert len(results) == 1 and results[0]["ok"] is True
    assert w.stats["jobs_completed"] == 1


def test_stop_mid_render_returns_promptly(monkeypatch):
    monkeypatch.setattr(worker_mod, "RENDER_HEARTBEAT_SECS", 0.2, raising=False)

    def very_slow_render(params):
        time.sleep(30)
        return b"\x89PNG-never"

    w, calls = _worker_with_fake_post(
        {"job": {"job_id": "job-10", "type": "media.render", "params": {"prompt": "x"}}},
        monkeypatch,
        render_impl=very_slow_render,
    )
    import threading

    t = threading.Thread(target=w._fetch_and_run, args=(IDENTITY,))
    t.start()
    time.sleep(0.5)
    w._stop_event.set()
    t.join(timeout=3)
    assert not t.is_alive()  # did not block on the 30s render
    results = [b for p, b in calls if p == worker_mod.JOB_RESULT_PATH]
    assert results == []  # abandoned job is not reported; hub times it out


def test_run_loop_survives_fetch_and_run_exception(monkeypatch, tmp_path):
    _write_identity(tmp_path, monkeypatch, IDENTITY)
    statuses = []
    w = HelperWorker(on_status=statuses.append)

    def fake_post(identity, path, body, timeout=30):
        return {"ok": True}

    w._post = fake_post
    w._system_idle = lambda: True

    def boom(identity):
        w._stop_event.set()
        raise RuntimeError("kaboom in job handling")

    w._fetch_and_run = boom
    w._run()  # returns after the guarded call; must not raise
    assert any("job handling failed" in s for s in statuses)
