"""Tailscale helpers for reaching the J.A.D.E. hub.

The hub is only reachable over Skyler's Tailnet. Volunteers join it with a
Skyler-generated auth key tagged ``tag:helper``.

ACL design (on Skyler's side, for reference):
    tag:helper may reach the hub's helper port ONLY. No other Tailnet host,
    no other port, no subnet route. The tag is assigned at key creation;
    volunteers never pick tags themselves.

The auth key is never logged or written to disk by this module — it goes
straight into the `tailscale up` subprocess arguments.
"""

import socket
import subprocess


def tailscale_up(auth_key: str) -> None:
    """Run `tailscale up --auth-key=<key>` so this machine joins the tailnet.

    The key is passed as a subprocess argument and is never logged.
    Raises RuntimeError if tailscale is missing or the join fails.
    """
    try:
        proc = subprocess.run(
            ["tailscale", "up", f"--auth-key={auth_key}"],
            capture_output=True,
            text=True,
            timeout=120,
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


def tailscale_status() -> dict:
    """Best-effort `tailscale status --json`. Returns {} on any failure."""
    try:
        proc = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return {}
        import json

        return json.loads(proc.stdout)
    except Exception:
        return {}
