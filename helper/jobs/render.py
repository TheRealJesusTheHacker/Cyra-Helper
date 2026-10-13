"""Local Stable Diffusion rendering for media.render jobs.

The hub hands the helper a prompt and parameters; the helper runs the
diffusion pipeline LOCALLY and returns PNG bytes. The pipeline is lazy-
loaded on the first job and kept in memory for the rest of the session.

Default model: runwayml/stable-diffusion-v1-5
    ~4GB one-time download from Hugging Face. We never download a
    multi-gig model without the volunteer's say-so: the first job that
    names an unapproved model triggers an approval prompt (GUI dialog in
    the app, y/n in console mode), and approvals are remembered in
    %APPDATA%/JadeHelper/known_models.json so each model is only asked
    about once. Pre-approve models with --allow-model (console).

Clamps (mirroring the hub): width/height 64..2048, steps 1..150.
Errors are honest: no usable GPU/device raises with a plain message.
"""

import io
import json
import os
import re

DEFAULT_MODEL = "runwayml/stable-diffusion-v1-5"

# Hub-supplied model ids must look like a plain Hugging Face repo id
# ("owner/repo"). from_pretrained would otherwise happily load local
# paths or crafted repo ids — a malicious hub could run code via
# downloaded model files. Anything else is rejected outright.
_MODEL_RE = re.compile(r"^[\w.\-]+/[\w.\-]+$")

# Models the volunteer has pre-approved without a prompt. The host wires
# this up: main.py (console --allow-model) and gui.py. Models approved
# interactively land in the persisted known-models file instead.
allowed_models: set = {DEFAULT_MODEL}

# Host-provided consent hook: consent_callback(model_id) -> bool. Called
# from the worker thread when a job names a model that is neither
# pre-approved nor in the persisted known-models file. gui.py sets this
# to a thread-safe GUI dialog; main.py sets it to a console prompt.
consent_callback = None

_MIN_DIM, _MAX_DIM = 64, 2048
_MIN_STEPS, _MAX_STEPS = 1, 150

_pipeline = None
_pipeline_model = None


def _known_models_path():
    from helper.registration import app_dir

    return app_dir() / "known_models.json"


def _load_known_models() -> set:
    """Models the volunteer approved in earlier sessions (persisted set)."""
    try:
        data = json.loads(_known_models_path().read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(m) for m in data if isinstance(m, str)}
    except (OSError, ValueError):
        pass
    return set()


def _save_known_models(models: set) -> None:
    try:
        p = _known_models_path()
        # JH3: write-then-replace so a crash mid-write can't corrupt the
        # approvals file (a torn file would re-prompt for every model).
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(sorted(models), indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _model_approved(model: str) -> bool:
    """True if the volunteer already approved ``model``.

    Checks the persisted known-models set and the host's pre-approvals
    first; otherwise asks the host's consent hook. An approval is
    remembered so each model is only asked about once. No hook (or a
    refusal) means no download.
    """
    known = _load_known_models()
    if model in known or model in allowed_models:
        return True
    ok = False
    if consent_callback is not None:
        try:
            ok = bool(consent_callback(model))
        except Exception:
            ok = False
    if ok:
        known.add(model)
        _save_known_models(known)
    return ok


def _clamp(value, lo, hi):
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = lo
    return max(lo, min(hi, v))


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _load_pipeline(model: str):
    """Load (once) the diffusers pipeline for ``model``. Keeps it cached."""
    global _pipeline, _pipeline_model
    if _pipeline is not None and _pipeline_model == model:
        return _pipeline
    if not _model_approved(model):
        raise RuntimeError(
            f"model {model!r} was not approved by the volunteer — "
            "approve new models in the app or pass --allow-model <owner/repo> "
            "in console mode"
        )
    try:
        from diffusers import StableDiffusionPipeline
    except ImportError as e:
        raise RuntimeError(
            "diffusers is not installed — install the requirements first"
        ) from e
    device = _device()
    try:
        import torch

        dtype = torch.float16 if device == "cuda" else torch.float32
    except Exception:
        dtype = None
    # use_safetensors: refuse pickle .bin weights entirely — a poisoned
    # checkpoint can't run code through torch.load if we never torch.load.
    # trust_remote_code=False: never execute model-repo code.
    pipe = StableDiffusionPipeline.from_pretrained(
        model, torch_dtype=dtype, use_safetensors=True, trust_remote_code=False
    )
    pipe = pipe.to(device)
    _pipeline, _pipeline_model = pipe, model
    return pipe


def render(params: dict) -> bytes:
    """Render a media.render job. Returns PNG bytes.

    params keys: prompt, negative_prompt, width, height, steps,
                 seed (int or None), model (str, optional).
    """
    prompt = params.get("prompt") or ""
    if not prompt.strip():
        raise ValueError("job params missing a prompt")

    model = (params.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if not _MODEL_RE.fullmatch(model):
        raise ValueError(f"invalid model identifier: {model!r}")
    # JH3: the regex alone lets "." / ".." segments through ("../foo",
    # "foo/.."), and from_pretrained treats an id that names an existing
    # relative path as a LOCAL directory. A hub-supplied id must only
    # ever be a Hugging Face repo id, never a filesystem path.
    if any(seg in (".", "..") for seg in model.split("/")):
        raise ValueError(f"invalid model identifier: {model!r}")
    # diffusers needs dims divisible by 8 (latent space is /8); round down
    # after clamping. 64..2048 clamping already guarantees >= 8-aligned 64.
    width = _clamp(params.get("width", 512), _MIN_DIM, _MAX_DIM) // 8 * 8
    height = _clamp(params.get("height", 512), _MIN_DIM, _MAX_DIM) // 8 * 8
    steps = _clamp(params.get("steps", 30), _MIN_STEPS, _MAX_STEPS)
    negative = params.get("negative_prompt") or ""

    pipe = _load_pipeline(model)

    generator = None
    seed = params.get("seed")
    if seed is not None:
        try:
            import torch

            generator = torch.Generator(device=_device()).manual_seed(int(seed))
        except Exception:
            generator = None

    image = pipe(
        prompt=prompt,
        negative_prompt=negative,
        width=width,
        height=height,
        num_inference_steps=steps,
        generator=generator,
    ).images[0]

    buf = io.BytesIO()
    # PNG is lossless at every compress_level — the decoded pixels are
    # identical; only the DEFLATE effort changes. Level 1 encodes ~5x
    # faster than PIL's default (6) for ~4% larger output (measured on
    # photo-like images at 1024^2 and 2048^2, 2026-10-13 perf pass),
    # which is pure per-job overhead removed from the volunteer's
    # machine; the size delta is negligible on the result POST.
    image.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()
