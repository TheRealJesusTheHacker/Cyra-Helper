"""Decision-wave tests (2026-10-21) — model-download size pre-check.

Before this change the consent prompt said "several GB" no matter what
the hub named — a consent prompt that understates what it asks consent
for is a false claim at the moment honesty matters most. Now the size
comes from Hugging Face hub metadata (_model_size_bytes), is passed to
the consent hook, and is stated in the prompt text both hosts show
(render.consent_message). When the lookup fails, the prompt says the
size is unknown — no vague figure is presented as fact, and no number
is ever estimated or fabricated.
"""

import importlib
import sys
import types
from pathlib import Path

import pytest

render_mod = importlib.import_module("helper.jobs.render")

MODEL = "fake-owner/fake-model"


# ------------------------------------------------------------- size lookup
def _stub_hub(monkeypatch, siblings=None, raises=False):
    fake = types.ModuleType("huggingface_hub")

    class _Info:
        def __init__(self):
            self.siblings = siblings

    class _Api:
        def model_info(self, model, files_metadata=False):
            assert files_metadata is True
            if raises:
                raise RuntimeError("hub unreachable (fake)")
            return _Info()

    fake.HfApi = _Api
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)


def test_size_sums_the_sizes_metadata_actually_returned(monkeypatch):
    _stub_hub(
        monkeypatch,
        siblings=[
            types.SimpleNamespace(size=2_000_000_000),
            types.SimpleNamespace(size=500_000_000),
            types.SimpleNamespace(size=None),  # unknown per-file: skipped
            types.SimpleNamespace(size="junk"),  # malformed: skipped
        ],
    )
    assert render_mod._model_size_bytes(MODEL) == 2_500_000_000


def test_size_lookup_failure_is_none_not_a_guess(monkeypatch):
    _stub_hub(monkeypatch, raises=True)
    assert render_mod._model_size_bytes(MODEL) is None


def test_size_with_no_usable_metadata_is_none(monkeypatch):
    _stub_hub(monkeypatch, siblings=[])
    assert render_mod._model_size_bytes(MODEL) is None
    _stub_hub(monkeypatch, siblings=[types.SimpleNamespace(size=None)])
    assert render_mod._model_size_bytes(MODEL) is None
    _stub_hub(monkeypatch, siblings=None)
    assert render_mod._model_size_bytes(MODEL) is None


# ------------------------------------------------------------ prompt wording
def test_known_size_is_shown_in_the_consent_text():
    msg = render_mod.consent_message(MODEL, 2_500_000_000)
    assert MODEL in msg
    assert "2.5 GB" in msg
    assert "unknown" not in msg.lower()


def test_failed_lookup_says_size_unknown_and_invents_no_number():
    msg = render_mod.consent_message(MODEL, None)
    assert MODEL in msg
    assert "size is unknown" in msg.lower()
    # The old vague claims must not reappear dressed as fact.
    assert "several" not in msg.lower()
    assert "GB" not in msg
    assert not any(ch.isdigit() for ch in msg)


def test_format_model_size():
    assert render_mod.format_model_size(None) == "size unknown"
    assert render_mod.format_model_size(4_200_000_000) == "4.2 GB"
    assert render_mod.format_model_size(850_000_000) == "850 MB"


# ------------------------------------------------------- the consent wiring
@pytest.fixture()
def _consent_env(monkeypatch):
    monkeypatch.setattr(render_mod, "_load_known_models", lambda: set())
    saved = []
    monkeypatch.setattr(render_mod, "_save_known_models", saved.append)
    return saved


def test_consent_hook_receives_the_real_size(monkeypatch, _consent_env):
    monkeypatch.setattr(render_mod, "_model_size_bytes", lambda m: 2_500_000_000)
    seen = {}

    def _consent(model, size_bytes):
        seen["model"], seen["size"] = model, size_bytes
        return True

    monkeypatch.setattr(render_mod, "consent_callback", _consent)
    assert render_mod._model_approved(MODEL) is True
    assert seen == {"model": MODEL, "size": 2_500_000_000}


def test_consent_hook_receives_none_when_lookup_fails(monkeypatch, _consent_env):
    monkeypatch.setattr(render_mod, "_model_size_bytes", lambda m: None)
    seen = {}

    def _consent(model, size_bytes):
        seen["size"] = size_bytes
        return False

    monkeypatch.setattr(render_mod, "consent_callback", _consent)
    assert render_mod._model_approved(MODEL) is False
    assert seen["size"] is None


def test_already_approved_model_triggers_no_lookup(monkeypatch):
    monkeypatch.setattr(render_mod, "_load_known_models", lambda: {MODEL})

    def _boom(model):
        raise AssertionError("size lookup ran for an approved model")

    monkeypatch.setattr(render_mod, "_model_size_bytes", _boom)
    monkeypatch.setattr(render_mod, "consent_callback", None)
    assert render_mod._model_approved(MODEL) is True


def test_no_consent_hook_means_no_lookup_and_no_download(monkeypatch, _consent_env):
    def _boom(model):
        raise AssertionError("size lookup ran with nobody to ask")

    monkeypatch.setattr(render_mod, "_model_size_bytes", _boom)
    monkeypatch.setattr(render_mod, "consent_callback", None)
    assert render_mod._model_approved(MODEL) is False


def test_legacy_one_argument_consent_hook_still_works(monkeypatch, _consent_env):
    monkeypatch.setattr(render_mod, "_model_size_bytes", lambda m: 123)
    monkeypatch.setattr(render_mod, "consent_callback", lambda m: True)
    assert render_mod._model_approved(MODEL) is True


def test_raising_consent_hook_still_means_refusal(monkeypatch, _consent_env):
    monkeypatch.setattr(render_mod, "_model_size_bytes", lambda m: 123)

    def _bad(model, size_bytes):
        raise RuntimeError("dialog died (fake)")

    monkeypatch.setattr(render_mod, "consent_callback", _bad)
    assert render_mod._model_approved(MODEL) is False


# ------------------------------------------------- hosts show the same text
def test_hosts_use_the_shared_consent_message():
    root = Path(__file__).resolve().parent.parent
    gui_src = (root / "helper" / "gui.py").read_text(encoding="utf-8")
    main_src = (root / "helper" / "main.py").read_text(encoding="utf-8")
    assert "consent_message" in gui_src
    assert "consent_message" in main_src
    # The understating wording this item removed, pinned absent.
    assert "several GB" not in gui_src
    assert "multi-GB" not in main_src
