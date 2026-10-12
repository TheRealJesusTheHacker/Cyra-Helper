"""Local Stable Diffusion rendering for media.render jobs.

The hub hands the helper a prompt and parameters; the helper runs the
diffusion pipeline LOCALLY and returns PNG bytes. The pipeline is lazy-
loaded on the first job and kept in memory for the rest of the session.

Default model: runwayml/stable-diffusion-v1-5
    ~4GB one-time download from Hugging Face. The volunteer opts in during
    setup — we never download a multi-gig model without asking first.

Clamps (mirroring the hub): width/height 64..2048, steps 1..150.
Errors are honest: no usable GPU/device raises with a plain message.
"""

import io
import re

DEFAULT_MODEL = "runwayml/stable-diffusion-v1-5"

# Hub-supplied model ids must look like a plain Hugging Face repo id
# ("owner/repo"). from_pretrained would otherwise happily load local
# paths or crafted repo ids — a malicious hub could run code via
# downloaded model files. Anything else is rejected outright.
_MODEL_RE = re.compile(r"^[\w.\-]+/[\w.\-]+$")

_MIN_DIM, _MAX_DIM = 64, 2048
_MIN_STEPS, _MAX_STEPS = 1, 150

_pipeline = None
_pipeline_model = None


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
    pipe = StableDiffusionPipeline.from_pretrained(model, torch_dtype=dtype)
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
    image.save(buf, format="PNG")
    return buf.getvalue()
