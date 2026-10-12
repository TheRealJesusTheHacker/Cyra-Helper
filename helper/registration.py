"""Identity + registration for a Jade Helper volunteer node.

Identity lives at %APPDATA%/JadeHelper/helper.json:
    {"body_id": "helper-<12 hex>", "token": "<urlsafe>", "hub_base": "...", "name": "..."}

The file is written with best-effort 0600 permissions (os.chmod is a
best-effort call on Windows — the note is in the docstring, not a promise).
The token is NEVER logged, printed, or put in an exception message.
"""

import json
import os
import re
import secrets
from pathlib import Path

import requests

from helper.protocol import PROTOCOL_V, REGISTER_PATH, valid_body_id

# Helper names: 1..64 chars, no control characters (JH2-8 — the old
# ".{1,64}" with DOTALL would accept newlines and other control chars).
NAME_RE = re.compile(r"[^\x00-\x1f]{1,64}")


class ProtocolMismatchError(Exception):
    """The hub speaks a different protocol version. Volunteers must update."""


class BlockedError(Exception):
    """The hub has blocklisted this helper's body_id."""


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "JadeHelper"
    d.mkdir(parents=True, exist_ok=True)
    return d


def identity_path() -> Path:
    return app_dir() / "helper.json"


def mint_body_id() -> str:
    """Generate a fresh body_id: 'helper-' + 12 lowercase hex chars."""
    return "helper-" + secrets.token_hex(6)


def load_identity() -> dict | None:
    """Return the stored identity dict, or None if absent/invalid."""
    p = identity_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not valid_body_id(data.get("body_id")) or not data.get("token"):
        return None
    return data


def save_identity(identity: dict) -> None:
    """Write the identity file. Permissions are best-effort 0600.

    On Windows, os.chmod only honors the read-only bit, so this is
    defense-in-depth, not a guarantee. The token is never logged anywhere.
    """
    p = identity_path()
    p.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)  # best-effort; Windows honors only read-only
    except OSError:
        pass


def delete_identity() -> None:
    """Unpair: remove the stored identity. The worker must be stopped first."""
    try:
        identity_path().unlink()
    except OSError:
        pass


def gather_capabilities() -> dict:
    """Probe the machine for the capabilities block sent at registration."""
    import psutil

    cpu_count = os.cpu_count() or 1
    ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 2)

    gpu_name, vram_gb, cuda = "none", 0.0, False
    try:
        import torch

        if torch.cuda.is_available():
            cuda = True
            gpu_name = torch.cuda.get_device_name(0)
            vram_gb = round(torch.cuda.get_device_properties(0).total_memory / (1024 ** 3), 2)
    except Exception:
        pass

    return {
        "cpu_count": int(cpu_count),
        "ram_gb": float(ram_gb),
        "gpu_name": str(gpu_name)[:256],
        "vram_gb": float(vram_gb),
        "cuda": bool(cuda),
    }


def register(hub_base: str, name: str, capabilities: dict) -> dict:
    """Register a new helper with the hub and persist the identity.

    POSTs {"protocol_v": 1, ...} to /helper/v1/register.

    Raises:
        ProtocolMismatchError: hub returned 400 — the hub's message is
            passed through so the volunteer sees "update your Jade Helper".
        BlockedError: hub returned 403 — this body_id is blocklisted.
        ValueError: name is empty or longer than 64 chars.
    """
    name = (name or "").strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("name must be 1..64 characters")

    hub_base = hub_base.rstrip("/")
    body_id = mint_body_id()
    body = {
        "protocol_v": PROTOCOL_V,
        "body_id": body_id,
        "name": name,
        "platform": "windows",
        "capabilities": capabilities,
    }
    resp = requests.post(hub_base + REGISTER_PATH, json=body, timeout=30)
    if resp.status_code == 400:
        err = resp.json().get("error", "protocol mismatch")
        raise ProtocolMismatchError(err)
    if resp.status_code == 403:
        err = resp.json().get("error", "blocked")
        raise BlockedError(err)
    resp.raise_for_status()
    data = resp.json()
    identity = {
        "body_id": body_id,
        "token": data["token"],
        "hub_base": hub_base,
        "name": name,
        "hub_id": data.get("hub_id", ""),
    }
    save_identity(identity)
    return identity
