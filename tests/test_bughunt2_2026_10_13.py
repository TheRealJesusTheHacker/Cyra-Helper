"""Bug-hunt round 2 regression tests — 2026-10-13.

Covers the BH2 findings (each demonstrated broken on the pre-fix code):
  * registration: a stored identity whose token is not a string was
    accepted, then _headers() raised TypeError on EVERY post — the
    worker wedged on "heartbeat failed" forever, never re-pairing.
  * registration: register() let a bare ValueError escape when a 200
    response body wasn't JSON (JH3 guarded the token lookup, not the
    .json() call itself).
  * worker: a job with a missing/empty/non-string job_id was rendered
    and reported under the bogus id — GPU burned for a result the hub
    cannot accept or dedupe.
  * render: _clamp let ±inf escape as OverflowError (JSON decoders that
    accept Infinity let a hub send it); non-string prompt/model died as
    AttributeError, non-string negative_prompt reached the pipeline.
  * render: concurrent _load_pipeline calls double-loaded the pipeline
    (check + assignment weren't atomic) — double VRAM, one pipe leaked.
  * main: --idle-cpu accepted out-of-range values (negative = never
    idle = never works; >100/NaN = always idle); --config accepted a
    non-string hub_base that crashed worker construction.
Plus a behavior pin: a hub that dies mid-job must not wedge the worker.
"""

import importlib
import json
import sys
import threading
import time
import types

import pytest

# importlib, not "import helper.jobs.render as …": helper.jobs.__init__
# re-exports the render *function* under the same attribute name.
render_mod = importlib.import_module("helper.jobs.render")
import helper.main as main_mod
import helper.registration as registration
import helper.worker as worker_mod
from helper.worker import HelperWorker

IDENTITY = {
    "body_id": "helper-abcdef012345",
    "token": "test-token",
    "hub_base": "http://hub.invalid:9999",
    "name": "Test Helper",
}


# --------------------------------------------------------------- registration
def _write_identity(tmp_path, monkeypatch, data):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    p = tmp_path / "JadeHelper" / "helper.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.mark.parametrize("bad_token", [12345, ["x"], {"k": 1}, True, "", None])
def test_load_identity_rejects_non_string_token(tmp_path, monkeypatch, bad_token):
    _write_identity(tmp_path, monkeypatch, {**IDENTITY, "token": bad_token})
    assert registration.load_identity() is None


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


def test_register_non_json_200_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        registration.requests, "post", lambda *a, **k: _FakeResp(200, json_raises=True)
    )
    with pytest.raises(RuntimeError, match="not JSON"):
        registration.register("http://hub.invalid:9999", "Name", {})


# --------------------------------------------------------------------- worker
def _worker_with_fake_post(job_payload, monkeypatch, render_impl=None):
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


@pytest.mark.parametrize(
    "job",
    [
        {"type": "media.render", "params": {"prompt": "x"}},  # missing
        {"job_id": "", "type": "media.render", "params": {"prompt": "x"}},
        {"job_id": 123, "type": "media.render", "params": {"prompt": "x"}},
        {"job_id": None, "type": "media.render", "params": {"prompt": "x"}},
    ],
)
def test_job_without_valid_id_is_not_rendered(monkeypatch, job):
    rendered = []
    w, calls = _worker_with_fake_post(
        {"job": job},
        monkeypatch,
        render_impl=lambda params: rendered.append(params) or b"\x89PNG",
    )
    w._fetch_and_run(IDENTITY)
    assert rendered == []  # no GPU burned on an undeliverable job
    results = [b for p, b in calls if p == worker_mod.JOB_RESULT_PATH]
    assert results == []  # and nothing reported under a bogus id
    assert w.busy is False


def test_hub_dying_mid_job_does_not_wedge_worker(monkeypatch):
    # Pin: result POST failing on both attempts (hub died mid-job) must
    # leave the worker usable — _fetch_and_run returns, busy resets.
    w = HelperWorker()
    statuses = []
    w.on_status = statuses.append

    def flaky_post(identity, path, body, timeout=30):
        if path == worker_mod.JOB_FETCH_PATH:
            return {"job": {"job_id": "job-h", "type": "media.render",
                            "params": {"prompt": "x"}}}
        if path == worker_mod.JOB_RESULT_PATH:
            raise ConnectionError("hub gone")
        return {"ok": True}

    w._post = flaky_post
    monkeypatch.setattr(worker_mod, "render", lambda params: b"\x89PNG")
    monkeypatch.setattr(worker_mod.time, "sleep", lambda s: None)
    w._fetch_and_run(IDENTITY)
    assert w.busy is False
    assert any("result post failed" in s for s in statuses)


# --------------------------------------------------------------------- render
class _FakeImage:
    def save(self, buf, format=None, **_kwargs):
        buf.write(b"\x89PNG-fake")


def _recording_pipe(seen):
    class _Pipe:
        def __call__(self, **kwargs):
            seen.update(kwargs)

            class _Out:
                images = [_FakeImage()]

            return _Out()

    return _Pipe()


def test_clamp_handles_infinity():
    assert render_mod._clamp(float("inf"), 64, 2048) == 2048
    assert render_mod._clamp(float("-inf"), 64, 2048) == 64


def test_render_with_infinite_dimensions_clamps(monkeypatch):
    seen = {}
    monkeypatch.setattr(render_mod, "_load_pipeline", lambda m: _recording_pipe(seen))
    png = render_mod.render({"prompt": "x", "width": float("inf"), "steps": float("inf")})
    assert png == b"\x89PNG-fake"
    assert seen["width"] == 2048
    assert seen["num_inference_steps"] == 150


def test_render_rejects_non_string_prompt_and_model(monkeypatch):
    monkeypatch.setattr(render_mod, "_load_pipeline", lambda m: _recording_pipe({}))
    for bad in ({"prompt": 123}, {"prompt": ["a", "dog"]}):
        with pytest.raises(ValueError):
            render_mod.render(bad)
    for bad in ({"prompt": "x", "model": 123}, {"prompt": "x", "model": ["a/b"]}):
        with pytest.raises(ValueError):
            render_mod.render(bad)


def test_render_drops_non_string_negative_prompt(monkeypatch):
    seen = {}
    monkeypatch.setattr(render_mod, "_load_pipeline", lambda m: _recording_pipe(seen))
    render_mod.render({"prompt": "x", "negative_prompt": 123})
    assert seen["negative_prompt"] == ""


def test_load_pipeline_loads_once_under_concurrency(monkeypatch):
    loads = []

    class _SlowPipe:
        def to(self, device):
            return self

    class _SDP:
        @staticmethod
        def from_pretrained(model, **kwargs):
            loads.append(model)
            time.sleep(0.3)
            return _SlowPipe()

    fake_diffusers = types.ModuleType("diffusers")
    fake_diffusers.StableDiffusionPipeline = _SDP
    monkeypatch.setitem(sys.modules, "diffusers", fake_diffusers)
    monkeypatch.setattr(render_mod, "_pipeline", None)
    monkeypatch.setattr(render_mod, "_pipeline_model", None)
    monkeypatch.setattr(render_mod, "_model_approved", lambda m: True)

    threads = [
        threading.Thread(target=render_mod._load_pipeline, args=("owner/model",))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert loads == ["owner/model"]  # exactly one (multi-GB / VRAM) load


# ----------------------------------------------------------------------- main
@pytest.mark.parametrize("bad", ["-1", "100.5", "nan", "inf"])
def test_console_rejects_out_of_range_idle_cpu(bad):
    with pytest.raises(SystemExit):
        main_mod.main(["--console", "--idle-cpu", bad])


def test_read_config_rejects_non_string_values(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"hub_base": 123, "name": "x"}), encoding="utf-8")
    assert main_mod._read_config(str(cfg)) == {}
    cfg.write_text(json.dumps({"hub_base": "http://h:1", "name": 7}), encoding="utf-8")
    assert main_mod._read_config(str(cfg)) == {}
    cfg.write_text(json.dumps({"hub_base": "http://h:1", "name": "ok"}), encoding="utf-8")
    assert main_mod._read_config(str(cfg))["hub_base"] == "http://h:1"
