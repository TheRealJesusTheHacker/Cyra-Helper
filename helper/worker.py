"""Background worker: heartbeat, idle detection, job fetch, execute, report.

Runs in a daemon thread. Cycle:
    1. Register with the hub if no identity is stored yet.
    2. Loop: POST heartbeat every 30s (psutil cpu/mem, free GPU VRAM).
       If not paused, not busy, and the system is idle
       (cpu < threshold, default 40%; no fullscreen app, best-effort)
       → fetch a job → run it via helper.jobs.render → POST the result.
    3. pause()/resume() are honored immediately via threading.Event.
    4. stop() shuts the loop down cleanly.

Never logs the token or the auth key.
"""

import base64
import ctypes
import os
import threading
import time
from pathlib import Path

import psutil
import requests

from helper.jobs.render import render
from helper.protocol import (
    HEARTBEAT_PATH,
    JOB_FETCH_PATH,
    JOB_RESULT_PATH,
    PROTOCOL_V,
)
from helper.registration import (
    BlockedError,
    ProtocolMismatchError,
    gather_capabilities,
    load_identity,
    register,
)

# While a render runs, the worker keeps sending busy heartbeats on this
# cadence so the hub doesn't mistake a long job for a dead helper.
RENDER_HEARTBEAT_SECS = 30


class HelperWorker:
    def __init__(
        self,
        hub_base: str | None = None,
        name: str | None = None,
        idle_cpu_threshold: float = 40.0,
        on_status=None,
        on_stats=None,
    ):
        """hub_base/name are only used for first registration; afterwards the
        stored identity rules. on_status(str) and on_stats(dict) callbacks
        feed the GUI (or console) with state changes."""
        self.hub_base = (hub_base or "").rstrip("/")
        self.name = name or "Jade Helper"
        self.idle_cpu_threshold = idle_cpu_threshold
        self.on_status = on_status or (lambda _s: None)
        self.on_stats = on_stats or (lambda _d: None)

        self._pause_event = threading.Event()  # set = paused
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = False
        self._stats = {"jobs_completed": 0, "images_rendered": 0, "seconds_contributed": 0.0}

    # ------------------------------------------------------------------ state
    def pause(self):
        self._pause_event.set()
        self.on_status("paused")

    def resume(self):
        self._pause_event.clear()
        self.on_status("idle")

    @property
    def paused(self) -> bool:
        return self._pause_event.is_set()

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="helper-worker")
        self._thread.start()

    def stop(self, timeout: float = 10.0):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    # ---------------------------------------------------------------- helpers
    def _headers(self, identity: dict) -> dict:
        return {"Authorization": "Bearer " + identity["token"]}

    @staticmethod
    def _scrub_paths(text: str) -> str:
        """Strip local path prefixes that can leak the volunteer's
        username (e.g. C:\\Users\\Skyler\\...) from text sent to the hub."""
        out = text
        for prefix in (
            os.environ.get("USERPROFILE"),
            os.environ.get("APPDATA"),
            os.environ.get("LOCALAPPDATA"),
            str(Path.home()),
        ):
            if prefix:
                out = out.replace(prefix, "<user-dir>")
        return out

    def _ensure_identity(self) -> dict:
        ident = load_identity()
        if ident:
            return ident
        self.on_status("registering")
        ident = register(self.hub_base, self.name, gather_capabilities())
        return ident

    def _free_vram_gb(self) -> float:
        try:
            import torch

            if torch.cuda.is_available():
                free, _ = torch.cuda.mem_get_info(0)
                return round(free / (1024 ** 3), 2)
        except Exception:
            pass
        return 0.0

    def _fullscreen_active(self) -> bool:
        """Best-effort check: is the foreground window fullscreen? (Windows)"""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            rect = ctypes.wintypes.RECT() if hasattr(ctypes, "wintypes") else None
            if rect is None:
                from ctypes import wintypes

                rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            return (rect.right - rect.left) >= w and (rect.bottom - rect.top) >= h
        except Exception:
            return False

    def _system_idle(self) -> bool:
        try:
            cpu = psutil.cpu_percent(interval=1.0)
        except Exception:
            cpu = 0.0
        if cpu >= self.idle_cpu_threshold:
            return False
        return not self._fullscreen_active()

    def _post(self, identity: dict, path: str, body: dict, timeout: int = 30):
        url = identity["hub_base"].rstrip("/") + path
        resp = requests.post(url, json=body, headers=self._headers(identity), timeout=timeout)
        if resp.status_code == 400:
            try:
                err = resp.json().get("error", "protocol mismatch")
            except Exception:
                err = "protocol mismatch"
            self.on_status(f"protocol mismatch: {err}")
            self._stop_event.set()
            return None
        if resp.status_code == 403:
            self.on_status("blocked")
            self._stop_event.set()
            return None
        if resp.status_code == 401:
            self.on_status("bad token — re-pair in the app")
            self._stop_event.set()
            return None
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------- loop
    def _run(self):
        try:
            identity = self._ensure_identity()
        except (ProtocolMismatchError, BlockedError, Exception) as e:
            self.on_status(f"registration failed: {e}")
            return
        self.on_status("registered")
        poll_secs = 30

        while not self._stop_event.is_set():
            paused_now = self.paused
            try:
                hb = {
                    "protocol_v": PROTOCOL_V,
                    "body_id": identity["body_id"],
                    "cpu_pct": float(psutil.cpu_percent(interval=None)),
                    "mem_pct": float(psutil.virtual_memory().percent),
                    "gpu_free_vram_gb": self._free_vram_gb(),
                    "busy": bool(self._busy),
                    "paused": bool(paused_now),
                }
                hb_resp = self._post(identity, HEARTBEAT_PATH, hb)
                if hb_resp is None:
                    break  # blocked or bad token — status already set
            except Exception as e:
                self.on_status(f"heartbeat failed: {e}")
                self._stop_event.wait(poll_secs)
                continue

            if paused_now:
                self.on_status("paused")
            elif not self._busy and self._system_idle():
                try:
                    self._fetch_and_run(identity)
                except Exception as e:
                    # JH3: job handling must never kill the worker loop —
                    # surface the failure and let the next cycle try again.
                    self.on_status(f"job handling failed: {e}")

            self._stop_event.wait(poll_secs)

    def _send_busy_heartbeat(self, identity: dict):
        """Best-effort busy heartbeat: tells the hub a render is running
        so a long job doesn't make this helper look dead. A failed POST
        must never cancel a render, so errors only set the status."""
        try:
            self._post(
                identity,
                HEARTBEAT_PATH,
                {
                    "protocol_v": PROTOCOL_V,
                    "body_id": identity["body_id"],
                    "cpu_pct": float(psutil.cpu_percent(interval=None)),
                    "mem_pct": float(psutil.virtual_memory().percent),
                    "gpu_free_vram_gb": self._free_vram_gb(),
                    "busy": True,
                    "paused": bool(self.paused),
                },
            )
        except Exception as e:
            self.on_status(f"busy heartbeat failed: {e}")

    def _fetch_and_run(self, identity: dict):
        try:
            fetch_resp = self._post(
                identity, JOB_FETCH_PATH, {"protocol_v": PROTOCOL_V, "body_id": identity["body_id"]}
            )
        except Exception as e:
            self.on_status(f"job fetch failed: {e}")
            return
        if fetch_resp is None:
            return
        # JH3: malformed hub payloads must never kill the worker thread —
        # before this guard, a non-dict response or job raised
        # AttributeError here, outside any try, and the daemon worker
        # thread died silently (GUI stuck on the last status forever).
        if not isinstance(fetch_resp, dict):
            self.on_status("job fetch failed: malformed hub response")
            return
        job = fetch_resp.get("job")
        if not job:
            self.on_status("idle")
            return
        if not isinstance(job, dict):
            self.on_status("job fetch failed: malformed job payload")
            return

        job_id = job.get("job_id", "")
        if job.get("type") != "media.render":
            self._report(identity, job_id, ok=False, image_b64=None, seconds=0.0,
                         error=f"unsupported job type: {job.get('type')}")
            return

        params = job.get("params")
        if not isinstance(params, dict):
            params = {}

        self._busy = True
        self.on_status("working")
        # Tell the hub we're busy right away so a long render doesn't make
        # us look dead before the next scheduled heartbeat.
        self._send_busy_heartbeat(identity)
        start = time.time()
        try:
            outcome: dict = {}

            def _render_target():
                try:
                    outcome["png"] = render(params)
                except Exception as e:  # reported below, scrubbed as before
                    outcome["error"] = e

            # JH3: render on a daemon thread and keep heartbeating while
            # it runs. Renders (CPU especially) can far outlast the 30s
            # poll; without this the hub sees a silent helper and may
            # requeue the job. Model-consent waits happen inside render,
            # so they are covered by these heartbeats too.
            render_thread = threading.Thread(
                target=_render_target, daemon=True, name="helper-render"
            )
            render_thread.start()
            slice_secs = max(0.05, min(1.0, RENDER_HEARTBEAT_SECS))
            last_hb = time.monotonic()
            while render_thread.is_alive():
                render_thread.join(timeout=slice_secs)
                if self._stop_event.is_set():
                    break
                if (
                    render_thread.is_alive()
                    and time.monotonic() - last_hb >= RENDER_HEARTBEAT_SECS
                ):
                    last_hb = time.monotonic()
                    self._send_busy_heartbeat(identity)
            if render_thread.is_alive():
                # Stop requested mid-render: don't block shutdown on the
                # GPU. The daemon thread finishes on its own and its
                # result is dropped — the hub times the job out, the same
                # net effect as the app being closed mid-render.
                return
            if "error" in outcome:
                raise outcome["error"]
            png = outcome["png"]
            elapsed = time.time() - start
            self._report(identity, job_id, ok=True,
                         image_b64=base64.b64encode(png).decode("ascii"),
                         seconds=elapsed, error=None)
            self._stats["jobs_completed"] += 1
            self._stats["images_rendered"] += 1
            self._stats["seconds_contributed"] += elapsed
            self.on_stats(self.stats)
        except Exception as e:
            elapsed = time.time() - start
            self._report(identity, job_id, ok=False, image_b64=None,
                         seconds=elapsed, error=self._scrub_paths(str(e))[:500])
        finally:
            self._busy = False
            if not self.paused:
                self.on_status("idle")

    def _report(self, identity: dict, job_id: str, ok: bool,
                image_b64: str | None, seconds: float, error: str | None):
        body = {
            "protocol_v": PROTOCOL_V,
            "body_id": identity["body_id"],
            "job_id": job_id,
            "ok": bool(ok),
            "image_b64": image_b64,
            "seconds": float(seconds),
            "error": error,
        }
        # JH2-10: the result is the whole point of the job — give it a
        # longer timeout and one retry before giving up.
        for attempt in (1, 2):
            try:
                self._post(identity, JOB_RESULT_PATH, body, timeout=60)
                return
            except Exception as e:
                if attempt == 2:
                    self.on_status(f"result post failed: {e}")
                else:
                    time.sleep(5)
