"""Sweep #3 (2026-10-21) regression tests — render seed honesty.

S3: render() used to swallow every unusable hub-supplied seed in a
blanket except and render UNSEEDED, then report success — the hub
received a different image than the seeded one it asked for, with no
signal. An unusable seed now fails the job honestly (ValueError for a
malformed shape; torch's own error for an out-of-range value), while
the shapes that always worked (int, integral float, digit string)
keep working.
"""
import importlib
import sys
import types

import pytest

render_mod = importlib.import_module("helper.jobs.render")


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


class _FakeGen:
    def __init__(self):
        self.seed = None

    def manual_seed(self, seed):
        # Mirror torch: seeds must fit in a uint64.
        if not (0 <= seed < 2 ** 64):
            raise RuntimeError("seed out of range")
        self.seed = seed
        return self


@pytest.fixture()
def fake_torch(monkeypatch):
    gens = []

    def _make_gen(device=None):
        g = _FakeGen()
        gens.append(g)
        return g

    fake = types.ModuleType("torch")
    fake.Generator = _make_gen
    fake.cuda = types.SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", fake)
    return gens


@pytest.fixture()
def pipe(monkeypatch):
    seen = {}
    monkeypatch.setattr(render_mod, "_load_pipeline",
                        lambda m: _recording_pipe(seen))
    return seen


def test_seed_int_is_used(fake_torch, pipe):
    png = render_mod.render({"prompt": "x", "seed": 42})
    assert png == b"\x89PNG-fake"
    assert pipe["generator"] is fake_torch[0]
    assert fake_torch[0].seed == 42


def test_seed_digit_string_still_works(fake_torch, pipe):
    render_mod.render({"prompt": "x", "seed": "42"})
    assert fake_torch[0].seed == 42


def test_seed_integral_float_still_works(fake_torch, pipe):
    render_mod.render({"prompt": "x", "seed": 42.0})
    assert fake_torch[0].seed == 42


@pytest.mark.parametrize("bad", ["abc", "42.5", 42.5, True, [42], {"s": 1}])
def test_seed_malformed_fails_honestly(fake_torch, pipe, bad):
    with pytest.raises(ValueError):
        render_mod.render({"prompt": "x", "seed": bad})


def test_seed_out_of_range_propagates_instead_of_silent_unseeded(fake_torch, pipe):
    with pytest.raises(RuntimeError):
        render_mod.render({"prompt": "x", "seed": 2 ** 100})


def test_no_seed_means_no_generator(fake_torch, pipe):
    render_mod.render({"prompt": "x"})
    assert pipe["generator"] is None
    assert fake_torch == []
