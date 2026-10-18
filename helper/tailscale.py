"""Tailscale helpers for reaching the Cyra hub.

The hub is only reachable over Skyler's Tailnet. Volunteers join it with a
Skyler-generated auth key tagged ``tag:helper``.

ACL design (on Skyler's side, for reference):
    tag:helper may reach the hub's helper port ONLY. No other Tailnet host,
    no other port, no subnet route. The tag is assigned at key creation;
    volunteers never pick tags themselves.

The auth key is never logged or written to disk by this module — it is
passed to `tailscale up` via the TS_AUTHKEY environment variable (so it
never appears in subprocess argv / Task Manager), and the child process
receives a copy of the environment containing it.
"""

import os
import socket
import subprocess


def tailscale_up(auth_key: str) -> None:
    """Run `tailscale up` so this machine joins the tailnet.

    The auth key is passed via the TS_AUTHKEY environment variable rather
    than `--auth-key=` argv, so it is not visible in process listings
    (e.g. Task Manager). It is never logged.
    Raises RuntimeError if tailscale is missing or the join fails.
    """
    env = dict(os.environ)
    env["TS_AUTHKEY"] = auth_key
    try:
        proc = subprocess.run(
            ["tailscale", "up"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except FileNotFoundError as e:
        raise RuntimeError("tailscale not found — install Tailscale first (see HELPER.md)") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("tailscale up timed out") from e
    if proc.returncode != 0:
        raise RuntimeError(f"tailscale up failed: {proc.stderr.strip() or proc.stdout.strip()}")


def hub_reachable(host: str, port: int, timeout: float = 10.0) -> bool:
    """TCP connect test to the hub's helper port. 10s timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
