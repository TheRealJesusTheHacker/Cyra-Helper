"""Cyra Helper entry point.

Normal launch: first-run setup wizard in the GUI (hub address + Tailscale
auth key) if this PC isn't registered yet, otherwise the main window.

    CyraHelper.exe [--console] [--config <path>]

--console   Headless worker, no GUI. Requires a stored identity (pair the
            PC in the GUI first) unless --config supplies hub + name.
--config    JSON file with {"hub_base": ..., "name": ...} used for an
            unattended first registration in console mode.
"""

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from helper.registration import load_identity


def _read_config(path: str | None) -> dict:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Could not read --config {path}: {e}", file=sys.stderr)
        return {}
    # JH2-7: a config that parses to a non-object (list, string, …) would
    # blow up later on .get(); reject it here instead.
    if not isinstance(data, dict):
        print(f"Could not read --config {path}: top level must be a JSON object",
              file=sys.stderr)
        return {}
    # BH2: hub_base/name must be strings — a numeric hub_base sails through
    # here and then crashes HelperWorker construction on .rstrip() with a
    # bare AttributeError. Reject the config loudly instead.
    for key in ("hub_base", "name"):
        if data.get(key) is not None and not isinstance(data[key], str):
            print(f"Could not read --config {path}: {key!r} must be a string",
                  file=sys.stderr)
            return {}
    return data


def _run_console(hub_base: str, name: str, idle_cpu: float,
                 allow_models: list | None = None) -> int:
    # importlib, not "import helper.jobs.render as …": the package's
    # __init__ re-exports the render *function* under the same attribute
    # name, which would shadow the module.
    import importlib
    render_mod = importlib.import_module("helper.jobs.render")
    from helper.worker import HelperWorker

    status_event = threading.Event()

    def on_status(text: str):
        print(f"[helper] {text}", flush=True)
        # JH2-1: exit cleanly on any terminal auth/protocol failure, not
        # just "blocked" — otherwise console mode hangs forever on 400/401.
        if text in ("blocked",) or text.startswith(
            ("registration failed", "protocol mismatch", "bad token")
        ):
            status_event.set()

    # JH2-3: pre-approve models named on the command line.
    render_mod.allowed_models |= {m.strip() for m in (allow_models or [])
                                 if m and m.strip()}

    def _console_consent(model: str) -> bool:
        """Ask the volunteer (interactive TTY) before the first download
        of an unknown model; refuse outright when there's nobody to ask."""
        if sys.stdin.isatty():
            try:
                ans = input(
                    f"[helper] The hub wants to download a new model: {model}\n"
                    "That's a multi-GB download the first time. Allow it? [y/N] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            return ans in ("y", "yes")
        print(f"[helper] refusing unknown model {model!r} — "
              "re-run with --allow-model <owner/repo> to permit it",
              file=sys.stderr)
        return False

    render_mod.consent_callback = _console_consent

    worker = HelperWorker(
        hub_base=hub_base,
        name=name,
        idle_cpu_threshold=idle_cpu,
        on_status=on_status,
        on_stats=lambda s: print(
            f"[helper] stats: {s['jobs_completed']} jobs, "
            f"{s['images_rendered']} images, "
            f"{s['seconds_contributed'] / 3600:.1f} h contributed",
            flush=True,
        ),
    )

    def _stop(*_):
        print("[helper] shutting down…", flush=True)
        worker.stop()
        status_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    worker.start()
    status_event.wait()
    worker.stop()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="CyraHelper",
                                     description="Volunteer compute client for Cyra")
    parser.add_argument("--console", action="store_true",
                        help="headless worker, no GUI")
    parser.add_argument("--config", metavar="PATH",
                        help="JSON config for unattended console registration")
    parser.add_argument("--idle-cpu", type=float, default=40.0,
                        help="CPU%% below which the machine counts as idle (default 40)")
    parser.add_argument("--allow-model", action="append", default=[],
                        metavar="MODEL",
                        help="pre-approve a Hugging Face model id (owner/repo) "
                             "for download, no prompt asked; repeatable")
    args = parser.parse_args(argv)

    # BH2: --idle-cpu is a percentage threshold, and out-of-range values
    # silently broke the idle gate in both directions: negative meant the
    # helper NEVER counted as idle (so it never took a job, heartbeating
    # forever), while >100 or NaN meant it ALWAYS counted as idle. Reject
    # loudly instead of running a helper that misbehaves in silence.
    if not 0.0 <= args.idle_cpu <= 100.0:
        parser.error(f"--idle-cpu must be between 0 and 100 (got {args.idle_cpu})")

    if not args.console:
        from helper.gui import run_gui

        run_gui()
        return 0

    cfg = _read_config(args.config)
    ident = load_identity()
    hub_base = (ident or {}).get("hub_base") or cfg.get("hub_base", "")
    name = (ident or {}).get("name") or cfg.get("name", "Cyra Helper")
    if not hub_base:
        print("[helper] No identity yet — run the GUI once to pair, "
              "or pass --config with hub_base.", file=sys.stderr)
        return 2
    return _run_console(hub_base, name, args.idle_cpu, args.allow_model)


if __name__ == "__main__":
    sys.exit(main())
