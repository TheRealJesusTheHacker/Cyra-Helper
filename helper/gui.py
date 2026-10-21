"""tkinter GUI — Bloodline-styled (near-black, blood-red accents).

First run (no stored identity): setup wizard asking for the hub address,
the Tailscale auth key, and a helper name. "Connect" runs `tailscale up`,
checks hub reachability, and registers — with progress shown.

Later runs: main window with a status line (Registered/Unregistered,
Idle/Working/Paused), contribution stats, Pause/Resume, and Unpair
(deletes the identity and stops the worker).
"""

import importlib
import threading
import tkinter as tk
from tkinter import messagebox

from helper.registration import (
    BlockedError,
    ProtocolMismatchError,
    delete_identity,
    gather_capabilities,
    load_identity,
    register,
)

# The render *module* (not the render function — helper.jobs.__init__
# re-exports the function under the same attribute name, so a plain
# "import helper.jobs.render as render_mod" would bind the function).
render_mod = importlib.import_module("helper.jobs.render")
from helper.tailscale import hub_reachable, tailscale_up
from helper.worker import HelperWorker

BG = "#0b0b0d"
PANEL = "#141417"
ACCENT = "#c1121f"
TEXT = "#e8e8e8"
DIM = "#8a8a90"


class _Base(tk.Tk):
    def __init__(self):
        super().__init__()
        # JH2-5/JH2-6: worker callbacks can land after the window is
        # destroyed. _dead + winfo_exists() guards (via _safe_after) keep
        # them from raising TclError on a dead widget.
        self._dead = False
        self.configure(bg=BG)
        self.geometry("400x560")
        self.resizable(False, False)
        self.title("Cyra Helper")
        self._style_widgets()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self):
        self._dead = True
        self.destroy()

    def _safe_after(self, ms, fn):
        """Like after(), but drops the callback if the window is already
        gone instead of raising TclError."""
        if self._dead:
            return

        def _guarded():
            if not self._dead and self.winfo_exists():
                fn()

        try:
            self.after(ms, _guarded)
        except tk.TclError:
            pass

    def _style_widgets(self):
        self.option_add("*Background", BG)
        self.option_add("*Foreground", TEXT)
        self.option_add("*Font", ("Segoe UI", 10))
        self.option_add("*Entry.Background", PANEL)
        self.option_add("*Entry.Foreground", TEXT)
        self.option_add("*Entry.InsertBackground", TEXT)
        self.option_add("*Button.Background", ACCENT)
        self.option_add("*Button.Foreground", TEXT)
        self.option_add("*Button.ActiveBackground", "#8f0d17")
        self.option_add("*Button.ActiveForeground", TEXT)
        self.option_add("*Button.Relief", tk.FLAT)
        self.option_add("*Button.PadX", 12)
        self.option_add("*Button.PadY", 6)

    def label(self, parent, text, **kw):
        pack_kw = kw.pop("pack_kw", {"pady": 4})
        kw.setdefault("bg", BG)
        kw.setdefault("fg", kw.pop("fg", TEXT))
        w = tk.Label(parent, text=text, **kw)
        w.pack(**pack_kw)
        return w

    def accent_button(self, parent, text, command):
        b = tk.Button(parent, text=text, command=command, bg=ACCENT, fg=TEXT,
                      activebackground="#8f0d17", relief=tk.FLAT, padx=12, pady=6)
        b.pack(pady=6)
        return b

    def ghost_button(self, parent, text, command):
        b = tk.Button(parent, text=text, command=command, bg=PANEL, fg=DIM,
                      activebackground="#1e1e22", relief=tk.FLAT, padx=12, pady=6)
        b.pack(pady=6)
        return b


class SetupWizard(_Base):
    """First-run: hub address + Tailscale auth key + helper name → Connect."""

    def __init__(self):
        super().__init__()
        self.title("Cyra Helper — setup")
        self.label(self, "CYRA HELPER", font=("Segoe UI", 18, "bold"), fg=ACCENT,
                   pack_kw={"pady": (28, 4)})
        self.label(self, "Donate your idle GPU to Cyra", fg=DIM, pack_kw={"pady": (0, 18)})

        self.label(self, "Hub address", fg=DIM, pack_kw={"pady": (6, 0)})
        self.hub_var = tk.StringVar(value="http://")
        self._entry(self.hub_var)

        self.label(self, "Helper name", fg=DIM, pack_kw={"pady": (6, 0)})
        self.name_var = tk.StringVar(value="Cyra Helper")
        self._entry(self.name_var)

        self.label(self, "Tailscale auth key (from Skyler)", fg=DIM, pack_kw={"pady": (6, 0)})
        self.key_var = tk.StringVar()
        self._entry(self.key_var, show="•")

        self.progress = tk.Label(self, text="", bg=BG, fg=DIM, wraplength=340)
        self.progress.pack(pady=(10, 0))

        self.connect_btn = self.accent_button(self, "Connect", self._connect)
        self.label(self, "The auth key joins Skyler's Tailnet so this PC can\n"
                         "reach the hub. It's never stored or logged.", fg=DIM,
                   font=("Segoe UI", 8), pack_kw={"pady": (12, 0)})

    def _entry(self, var, **kw):
        e = tk.Entry(self, textvariable=var, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                     relief=tk.FLAT, width=38, **kw)
        e.pack(pady=2, ipady=5)
        return e

    def _say(self, text):
        self._safe_after(0, lambda: self.progress.configure(text=text))

    def _connect(self):
        hub = self.hub_var.get().strip().rstrip("/")
        name = self.name_var.get().strip()
        key = self.key_var.get().strip()
        if not hub.startswith(("http://", "https://")):
            messagebox.showerror("Setup", "Hub address must start with http:// or https://")
            return
        if not name:
            messagebox.showerror("Setup", "Give your helper a name (1–64 characters).")
            return
        if not key:
            messagebox.showerror("Setup", "Paste the Tailscale auth key from Skyler.")
            return
        self.connect_btn.configure(state=tk.DISABLED)
        threading.Thread(target=self._do_connect, args=(hub, name, key), daemon=True).start()

    def _do_connect(self, hub, name, key):
        try:
            self._say("Joining Skyler's Tailnet…")
            tailscale_up(key)

            self._say("Checking the hub is reachable…")
            host_port = hub.split("://", 1)[1]
            if ":" in host_port:
                host, port_s = host_port.rsplit(":", 1)
                port = int(port_s.split("/")[0])
            else:
                host, port = host_port.split("/")[0], 443 if hub.startswith("https") else 80
            if not hub_reachable(host, port):
                raise RuntimeError("Hub not reachable — check the address and Tailscale.")

            self._say("Registering with the hub…")
            register(hub, name, gather_capabilities())

            self._say("Done — launching…")
            self.after(0, self._relaunch)
        except (ProtocolMismatchError, BlockedError, RuntimeError) as e:
            self._say("")
            self._safe_after(0, lambda: messagebox.showerror("Setup", str(e)))
            self._safe_after(0, lambda: self.connect_btn.configure(state=tk.NORMAL))
        except Exception as e:
            self._say("")
            self._safe_after(0, lambda: messagebox.showerror("Setup", f"Connection failed: {e}"))
            self._safe_after(0, lambda: self.connect_btn.configure(state=tk.NORMAL))

    def _relaunch(self):
        self._dead = True
        self.destroy()
        MainWindow().mainloop()


class MainWindow(_Base):
    """Main window: status, stats, Pause/Resume, Unpair."""

    def __init__(self):
        super().__init__()
        self.title("Cyra Helper")

        self.label(self, "CYRA HELPER", font=("Segoe UI", 18, "bold"), fg=ACCENT,
                   pack_kw={"pady": (24, 2)})
        self.name_label = self.label(self, "", fg=DIM, pack_kw={"pady": (0, 12)})

        self.status_var = tk.StringVar(value="starting…")
        self.label(self, "Status", fg=DIM, font=("Segoe UI", 8), pack_kw={"pady": (8, 0)})
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=TEXT,
                 font=("Segoe UI", 14, "bold")).pack(pady=2)

        self.label(self, "Contribution", fg=DIM, font=("Segoe UI", 8), pack_kw={"pady": (14, 0)})
        self.stats_var = tk.StringVar(value="—")
        tk.Label(self, textvariable=self.stats_var, bg=BG, fg=TEXT,
                 font=("Segoe UI", 11), justify=tk.CENTER).pack(pady=4)

        self.pause_btn = self.accent_button(self, "Pause", self._toggle_pause)
        self.ghost_button(self, "Unpair this PC", self._unpair)

        self.label(self, "Runs in the background on spare resources only.\n"
                         "Pause any time. Never touches Cyra's memory\n"
                         "or conversations — render prompts only.", fg=DIM,
                   font=("Segoe UI", 8), pack_kw={"pady": (10, 0)})

        self.worker = None
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.after(100, self._start_worker)

    # ------------------------------------------------------------- worker glue
    def _start_worker(self):
        ident = load_identity()
        if not ident:
            self.destroy()
            SetupWizard().mainloop()
            return
        self.name_label.configure(text=ident.get("name", ""))
        # JH2-3: the GUI side of model approval — a job naming an unknown
        # model pops a yes/no dialog; the answer is remembered on disk.
        render_mod.consent_callback = self._ask_model_consent
        self.worker = HelperWorker(
            on_status=self._on_status,
            on_stats=self._on_stats,
        )
        self.worker.start()

    def _ask_model_consent(self, model: str, size_bytes=None) -> bool:
        """Called on the worker thread when a job names an unapproved
        model. Marshals the question onto the GUI thread and waits for
        the volunteer's answer (up to 5 minutes, then refuse).

        The dialog text comes from render.consent_message: it states
        the model's real size from hub metadata, or says the size is
        unknown when the lookup failed — never a vague figure."""
        answer: dict = {}
        done = threading.Event()

        def _ask():
            try:
                answer["ok"] = messagebox.askyesno(
                    "New model",
                    render_mod.consent_message(model, size_bytes),
                    parent=self,
                )
            except Exception:
                answer["ok"] = False
            finally:
                done.set()

        self._safe_after(0, _ask)
        done.wait(300)
        return bool(answer.get("ok"))

    def _on_status(self, text):
        self._safe_after(0, lambda: self.status_var.set(text.capitalize()))

    def _on_stats(self, stats):
        hours = stats["seconds_contributed"] / 3600
        text = (f"Jobs completed: {stats['jobs_completed']}   •   "
                f"Images rendered: {stats['images_rendered']}\n"
                f"Time contributed: {hours:.1f} h")
        self._safe_after(0, lambda: self.stats_var.set(text))

    # ---------------------------------------------------------------- actions
    def _toggle_pause(self):
        if not self.worker:
            return
        if self.worker.paused:
            self.worker.resume()
            self.pause_btn.configure(text="Pause")
        else:
            self.worker.pause()
            self.pause_btn.configure(text="Resume")

    def _unpair(self):
        if not messagebox.askyesno("Unpair",
                                   "Unpair this PC? Your identity is deleted and\n"
                                   "the worker stops. You can re-pair any time."):
            return
        if self.worker:
            self.worker.stop()
        delete_identity()
        self._dead = True
        self.destroy()
        SetupWizard().mainloop()

    def _quit(self):
        self._dead = True
        if self.worker:
            self.worker.stop()
        self.destroy()


def run_gui():
    """Launch the GUI: setup wizard on first run, main window otherwise."""
    if load_identity():
        MainWindow().mainloop()
    else:
        SetupWizard().mainloop()
