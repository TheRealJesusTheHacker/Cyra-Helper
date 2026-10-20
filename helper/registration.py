"""Identity + registration for a Cyra Helper volunteer node.

Identity lives at %APPDATA%/CyraHelper/helper.json:
    {"body_id": "helper-<12 hex>", "token": "<urlsafe>", "hub_base": "...", "name": "..."}

The file is written with best-effort 0600 permissions (os.chmod is a
best-effort call on Windows — the note is in the docstring, not a promise).
The token is NEVER logged, printed, or put in an exception message.
"""

import json
import os
import re
import secrets
import urllib.parse
from pathlib import Path

import psutil
import requests

from helper.protocol import PROTOCOL_V, REGISTER_PATH, valid_body_id

# Helper names: 1..64 chars, no control characters (JH2-8 — the old
# ".{1,64}" with DOTALL would accept newlines and other control chars).
NAME_RE = re.compile(r"[^\x00-\x1f]{1,64}")

# Hub tokens are urlsafe by contract (protocol.py: "<urlsafe>"; the hub
# mints them with secrets.token_urlsafe). Anything outside that alphabet
# is not just malformed, it is unusable: a token containing CR/LF, a
# space, or a character requests cannot encode in a header makes EVERY
# authenticated POST raise InvalidHeader, so the worker wedges on
# "heartbeat failed" forever instead of offering re-pairing (the same
# wedge family the token-type and hub_base-scheme checks closed; those
# checks only required str, so a hostile/corrupt token string sailed
# through). The length bound keeps a broken hub from persisting an
# unbounded bearer string to disk and into every header.
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-=]{1,512}")


def valid_token(token: object) -> bool:
    """Return True iff ``token`` is a usable urlsafe bearer token."""
    return isinstance(token, str) and bool(TOKEN_RE.fullmatch(token))


def valid_hub_base(base: object) -> bool:
    """Return True iff ``base`` is a hub URL requests can actually use.

    The scheme-prefix check alone accepted "http://" (no host),
    "http://hub:99999999" (port out of range) and "http://hub name"
    (whitespace): every POST to those raises InvalidURL/InvalidHeader
    and the worker spins on "heartbeat failed" forever instead of
    treating the stored identity as corrupt and offering re-pairing.
    Require a parseable URL with a real host and, when present, a
    valid port, and no whitespace/control characters anywhere.
    """
    if not isinstance(base, str) or not base:
        return False
    if not base.startswith(("http://", "https://")):
        return False
    if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in base):
        return False
    try:
        parts = urllib.parse.urlsplit(base)
        if not parts.hostname:
            return False
        _ = parts.port  # raises ValueError on a bad/out-of-range port
    except ValueError:
        return False
    return True


class ProtocolMismatchError(Exception):
    """The hub speaks a different protocol version. Volunteers must update."""


class BlockedError(Exception):
    """The hub has blocklisted this helper's body_id."""


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "CyraHelper"
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
    if not valid_body_id(data.get("body_id")):
        return None
    # The bearer token must be a string: _headers() concatenates it, and a
    # non-string token (hand-edited/corrupt file) would raise TypeError on
    # EVERY post, wedging the worker on "heartbeat failed" forever instead
    # of offering re-pairing (BH2: same family as the JH3 hub_base fix).
    token = data.get("token")
    if not valid_token(token):
        return None
    # JH3: an identity without hub_base is corrupt — the worker would
    # KeyError on every POST and spin on "heartbeat failed" forever
    # instead of ever offering re-pairing. register() always writes one.
    if not isinstance(data.get("hub_base"), str) or not data["hub_base"]:
        return None  # (full URL-shape validation is valid_hub_base, below)
    # Same wedge, one step later: a hub_base requests can't speak
    # (a hand-edited scheme, a bare "hub:9999", …) makes every POST
    # raise InvalidSchema/MissingSchema, and the worker spins on
    # "heartbeat failed" forever instead of offering re-pairing. The
    # hub is HTTP(S) by contract — register()/the wizard only ever
    # write http:// or https:// — so anything else is corrupt.
    if not valid_hub_base(data.get("hub_base")):
        return None
    return data


def save_identity(identity: dict) -> None:
    """Write the identity file. Permissions are best-effort 0600.

    On Windows, os.chmod only honors the read-only bit, so this is
    defense-in-depth, not a guarantee. The token is never logged anywhere.
    """
    p = identity_path()
    # JH3: write-then-replace so a crash mid-write can't leave a torn
    # identity file behind (a torn file loads as "no identity" and the
    # volunteer's pairing is silently lost).
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    try:
        # Restrict the tmp file BEFORE the replace: previously the token
        # sat in a file with default (umask) permissions until the chmod
        # after os.replace, and a crash in between left it that way.
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)  # best-effort; Windows honors only read-only
    except OSError:
        pass


def delete_identity() -> None:
    """Unpair: remove the stored identity. The worker must be stopped first."""
    p = identity_path()
    try:
        p.unlink()
    except OSError:
        pass
    # A crash mid-save can leave helper.json.tmp behind — it holds a
    # copy of the token, so unpairing must remove it too, not just the
    # live identity file.
    try:
        p.with_name(p.name + ".tmp").unlink()
    except OSError:
        pass


def gather_capabilities() -> dict:
    """Probe the machine for the capabilities block sent at registration."""
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


def _hub_error(resp, default: str) -> str:
    """The hub's error string from a response, or ``default`` when the
    body isn't the JSON contract (an error page, a proxy, …). Mirrors
    the guarded parse in helper.worker._post (JH3)."""
    try:
        err = resp.json().get("error", default)
        # Cap a hub-supplied string before it reaches a dialog/status
        # line: a broken or hostile hub can return an unbounded error
        # body, and 500 chars matches the result-error cap in worker.py.
        return err[:500] if isinstance(err, str) else default
    except Exception:
        return default


def register(hub_base: str, name: str, capabilities: dict) -> dict:
    """Register a new helper with the hub and persist the identity.

    POSTs {"protocol_v": 1, ...} to /helper/v1/register.

    Raises:
        ProtocolMismatchError: hub returned 400 — the hub's message is
            passed through so the volunteer sees "update your Cyra Helper".
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
        raise ProtocolMismatchError(_hub_error(resp, "protocol mismatch"))
    if resp.status_code == 403:
        raise BlockedError(_hub_error(resp, "blocked"))
    resp.raise_for_status()
    # BH2: the 200 body may not be JSON at all (proxy page, captive
    # portal, …). JH3 guarded the token lookup but not this call — a bare
    # ValueError escaped register() with a parser message no volunteer
    # can act on. Fail with a clear message instead.
    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"hub registration response was not JSON: {e}") from e
    # JH3: a 200 without a token is a broken hub response — fail with a
    # clear message, not a bare KeyError.
    token = data.get("token") if isinstance(data, dict) else None
    if not valid_token(token):
        # A non-string token (123, {...}) is just as broken: save_identity
        # would persist it, load_identity would reject it on the next
        # launch, and in this session _headers()'s "Bearer " + token
        # would TypeError on every POST — the wedge load_identity's own
        # token-type check guards against, arriving via the hub instead
        # of a corrupt file. Fail at registration, where it happened.
        raise RuntimeError("hub registration response did not include a token")
    identity = {
        "body_id": body_id,
        "token": token,
        "hub_base": hub_base,
        "name": name,
        # hub_id is informational only (protocol: str). A non-string
        # value from a broken hub is dropped, not persisted verbatim,
        # and a string one is capped like the other hub-supplied fields.
        "hub_id": data.get("hub_id", "")[:256]
        if isinstance(data.get("hub_id", ""), str)
        else "",
    }
    save_identity(identity)
    return identity
