"""Decision-wave tests (2026-10-21) — JH2-11 opt-in audit log.

The log answers one exposure: a compromised hub could make volunteers
render arbitrary content with zero local trace. The decided shape is
option (a): a LOCAL log of job id / model / timestamp / prompt, written
ONLY when the volunteer enables it (CYRA_HELPER_AUDIT_LOG env var or
the host-set opt-in flag). Default OFF must be byte-for-byte today's
behaviour: nothing written, no code path in the feature reached.

All prompts below are obvious fakes — no real prompt content in tests.
"""

import importlib
import json
from datetime import datetime

import pytest

render_mod = importlib.import_module("helper.jobs.render")
import helper.worker as worker_mod
from helper.worker import HelperWorker

IDENTITY = {
    "body_id": "helper-abcdef012345",
    "token": "test-token",
    "hub_base": "http://hub.invalid:9999",
    "name": "Test Helper",
}

FAKE_PARAMS = {"prompt": "fake prompt for audit test", "model": "fake-owner/fake-model"}


@pytest.fixture(autouse=True)
def _audit_off_by_default(monkeypatch):
    monkeypatch.delenv(render_mod.AUDIT_LOG_ENV, raising=False)
    monkeypatch.setattr(render_mod, "audit_log_opt_in", False)


def _audit_file(tmp_path):
    return tmp_path / "CyraHelper" / "audit_log.jsonl"


# ------------------------------------------------------------- the OFF pin
def test_default_off_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    render_mod.record_render_audit("job-1", FAKE_PARAMS)
    assert not _audit_file(tmp_path).exists()


def test_default_off_never_resolves_the_log_path(monkeypatch, tmp_path):
    # Stronger than "no file": with the setting off, the feature's path
    # code is unreachable — record_render_audit returns before it.
    monkeypatch.setenv("APPDATA", str(tmp_path))

    def _boom():
        raise AssertionError("audit log path resolved while disabled")

    monkeypatch.setattr(render_mod, "_audit_log_path", _boom)
    render_mod.record_render_audit("job-1", FAKE_PARAMS)  # must not raise


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "truthy-ish"])
def test_non_truthy_env_values_stay_off(monkeypatch, tmp_path, value):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv(render_mod.AUDIT_LOG_ENV, value)
    assert render_mod.audit_log_enabled() is False
    render_mod.record_render_audit("job-1", FAKE_PARAMS)
    assert not _audit_file(tmp_path).exists()


# ------------------------------------------------------------ the ON writes
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_opt_in_enables(monkeypatch, value):
    monkeypatch.setenv(render_mod.AUDIT_LOG_ENV, value)
    assert render_mod.audit_log_enabled() is True


def test_host_flag_opt_in_enables(monkeypatch):
    monkeypatch.setattr(render_mod, "audit_log_opt_in", True)
    assert render_mod.audit_log_enabled() is True


def test_enabled_writes_the_four_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv(render_mod.AUDIT_LOG_ENV, "1")
    render_mod.record_render_audit("job-42", FAKE_PARAMS)
    lines = _audit_file(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert set(entry) == {"job_id", "model", "timestamp", "prompt"}
    assert entry["job_id"] == "job-42"
    assert entry["model"] == "fake-owner/fake-model"
    assert entry["prompt"] == "fake prompt for audit test"
    # A real, timezone-aware timestamp — not a placeholder string.
    ts = datetime.fromisoformat(entry["timestamp"])
    assert ts.tzinfo is not None


def test_enabled_defaults_model_like_render_does(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv(render_mod.AUDIT_LOG_ENV, "true")
    render_mod.record_render_audit("job-7", {"prompt": "fake prompt, no model named"})
    entry = json.loads(_audit_file(tmp_path).read_text(encoding="utf-8"))
    assert entry["model"] == render_mod.DEFAULT_MODEL


def test_enabled_appends_one_line_per_job(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv(render_mod.AUDIT_LOG_ENV, "1")
    render_mod.record_render_audit("job-1", FAKE_PARAMS)
    render_mod.record_render_audit("job-2", FAKE_PARAMS)
    lines = _audit_file(tmp_path).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["job_id"] for line in lines] == ["job-1", "job-2"]


def test_audit_write_failure_never_raises(monkeypatch, tmp_path):
    # The log is best-effort: an unwritable path must not fail the job.
    monkeypatch.setenv(render_mod.AUDIT_LOG_ENV, "1")
    blocker = tmp_path / "audit_log.jsonl"
    blocker.mkdir()  # a directory where the file belongs -> open() fails
    monkeypatch.setattr(render_mod, "_audit_log_path", lambda: blocker)
    render_mod.record_render_audit("job-1", FAKE_PARAMS)  # must not raise


# ------------------------------------------------------- through the worker
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


_JOB = {
    "job": {
        "job_id": "job-audit-1",
        "type": "media.render",
        "params": {"prompt": "fake worker prompt", "model": "fake-owner/fake-model"},
    }
}


def test_worker_writes_audit_entry_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv(render_mod.AUDIT_LOG_ENV, "1")
    w, calls = _worker_with_fake_post(
        _JOB, monkeypatch, render_impl=lambda params: b"\x89PNG-fake"
    )
    w._fetch_and_run(IDENTITY)
    results = [b for p, b in calls if p == worker_mod.JOB_RESULT_PATH]
    assert results and results[0]["ok"] is True  # the job itself worked
    entry = json.loads(_audit_file(tmp_path).read_text(encoding="utf-8"))
    assert entry["job_id"] == "job-audit-1"
    assert entry["model"] == "fake-owner/fake-model"
    assert entry["prompt"] == "fake worker prompt"
    assert entry["timestamp"]


def test_worker_writes_nothing_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    w, calls = _worker_with_fake_post(
        _JOB, monkeypatch, render_impl=lambda params: b"\x89PNG-fake"
    )
    w._fetch_and_run(IDENTITY)
    results = [b for p, b in calls if p == worker_mod.JOB_RESULT_PATH]
    assert results and results[0]["ok"] is True
    assert not _audit_file(tmp_path).exists()
