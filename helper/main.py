"""Jade Helper entry point.

Normal launch: first-run setup wizard in the GUI (hub address + Tailscale
auth key) if this PC isn't registered yet, otherwise the main window.

    JadeHelper.exe [--console] [--config <path>]

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
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Could not read --config {path}: {e}", file=sys.stderr)
        return {}


def _run_console(hub_base: str, name: str, idle_cpu: float) -> int:
    from helper.worker import HelperWorker

    status_event = threading.Event()

    def on_status(text: str):
        print(f"[helper] {text}", flush=True)
        if text in ("blocked",) or text.startswith("registration failed"):
            status_event.set()

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
    parser = argparse.ArgumentParser(prog="JadeHelper",
                                     description="Volunteer compute client for J.A.D.E.")
    parser.add_argument("--console", action="store_true",
                        help="headless worker, no GUI")
    parser.add_argument("--config", metavar="PATH",
                        help="JSON config for unattended console registration")
    parser.add_argument("--idle-cpu", type=float, default=40.0,
                        help="CPU%% below which the machine counts as idle (default 40)")
    args = parser.parse_args(argv)

    if not args.console:
        from helper.gui import run_gui

        run_gui()
        return 0

    cfg = _read_config(args.config)
    ident = load_identity()
    hub_base = (ident or {}).get("hub_base") or cfg.get("hub_base", "")
    name = (ident or {}).get("name") or cfg.get("name", "Jade Helper")
    if not hub_base:
        print("[helper] No identity yet — run the GUI once to pair, "
              "or pass --config with hub_base.", file=sys.stderr)
        return 2
    return _run_console(hub_base, name, args.idle_cpu)


if __name__ == "__main__":
    sys.exit(main())
