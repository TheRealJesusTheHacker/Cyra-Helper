"""Helper↔hub protocol contract (v1).

This file is the contract. Every field name below must match the hub
implementation byte-for-byte. Both sides exchange ``protocol_v`` on EVERY
request; the hub rejects mismatches, and the client surfaces the hub's
"update your helper" message to the volunteer.

Base path: /helper/v1. All requests/responses are JSON.
Authenticated endpoints take ``Authorization: Bearer <token>``.

Endpoints
---------
POST /helper/v1/register
    Request::
        {"protocol_v": 1, "body_id": "helper-<12 lowercase hex chars>",
         "name": "<=64 chars>", "platform": "windows",
         "capabilities": {"cpu_count": int, "ram_gb": float,
                          "gpu_name": str, "vram_gb": float, "cuda": bool}}
    200: {"ok": true, "token": "<urlsafe>", "hub_id": str, "poll_secs": 30}
    400: {"ok": false, "error": "protocol mismatch: hub speaks v1 — update your Cyra Helper"}
    403: {"ok": false, "error": "blocked"}

POST /helper/v1/heartbeat
    Request::
        {"protocol_v": 1, "body_id": str, "cpu_pct": float, "mem_pct": float,
         "gpu_free_vram_gb": float, "busy": bool, "paused": bool}
    200: {"ok": true}
    401: {"ok": false, "error": "bad token"}
    403: {"ok": false, "error": "blocked"}

POST /helper/v1/job/fetch
    Request::
        {"protocol_v": 1, "body_id": str}
    200: {"ok": true, "job": null}
       OR: {"ok": true, "job": {"job_id": "job-<hex>", "type": "media.render",
             "params": {"prompt": str, "negative_prompt": str, "width": int,
                        "height": int, "steps": int, "seed": int|null,
                        "model": str}}}

POST /helper/v1/job/result
    Request::
        {"protocol_v": 1, "body_id": str, "job_id": str, "ok": bool,
         "image_b64": str|null, "seconds": float, "error": str|null}
        # image_b64 holds PNG bytes, base64-encoded, when ok is true.
    200: {"ok": true, "accepted": bool, "reason": str|null}

Design notes
------------
* Open registration: the hub auto-provisions any helper that registers;
  there is no approval gate. The hub keeps a blocklist/revoke keyed by
  body_id for misbehaving helpers.
* The helper never receives Cyra's memory, conversations, tools, or
  files — only render prompts and coarse resource stats.
* Clamps are mirrored on both sides: width/height 64..2048, steps 1..150.
"""

import re

PROTOCOL_V = 1

BASE_PATH = "/helper/v1"
REGISTER_PATH = BASE_PATH + "/register"
HEARTBEAT_PATH = BASE_PATH + "/heartbeat"
JOB_FETCH_PATH = BASE_PATH + "/job/fetch"
JOB_RESULT_PATH = BASE_PATH + "/job/result"

BODY_ID_RE = re.compile(r"^helper-[0-9a-f]{12}$")


def valid_body_id(bid: object) -> bool:
    """Return True iff ``bid`` matches ``^helper-[0-9a-f]{12}$``."""
    return isinstance(bid, str) and bool(BODY_ID_RE.match(bid))
