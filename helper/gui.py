"""tkinter GUI — Bloodline-styled (near-black, blood-red accents).

First run (no stored identity): setup wizard asking for the hub address,
the Tailscale auth key, and a helper name. "Connect" runs `tailscale up`,
checks hub reachability, and registers — with progress shown.

Later runs: main window with a status line (Registered/Unregistered,
Idle/Working/Paused), contribution stats, Pause/Resume, and Unpair
(deletes the identity and stops the worker).
"""

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
        self.configure(bg=BG)
        self.geometry("400x560")
        self.resizable(False, False)
        self.title("Jade Helper")
        self._style_widgets()

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
        self.title("Jade Helper — setup")
        self.label(self, "JADE HELPER", font=("Segoe UI", 18, "bold"), fg=ACCENT,
                   pack_kw={"pady": (28, 4)})
        self.label(self, "Donate your idle GPU to J.A.D.E.", fg=DIM, pack_kw={"pady": (0, 18)})

        self.label(self, "Hub address", fg=DIM, pack_kw={"pady": (6, 0)})
        self.hub_var = tk.StringVar(value="http://")
        self._entry(self.hub_var)

        self.label(self, "Helper name", fg=DIM, pack_kw={"pady": (6, 0)})
        self.name_var = tk.StringVar(value="Jade Helper")
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
        self.after(0, lambda: self.progress.configure(text=text))

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
            self.after(0, lambda: messagebox.showerror("Setup", str(e)))
            self.after(0, lambda: self.connect_btn.configure(state=tk.NORMAL))
        except Exception as e:
            self._say("")
            self.after(0, lambda: messagebox.showerror("Setup", f"Connection failed: {e}"))
            self.after(0, lambda: self.connect_btn.configure(state=tk.NORMAL))

    def _relaunch(self):
        self.destroy()
        MainWindow().mainloop()


class MainWindow(_Base):
    """Main window: status, stats, Pause/Resume, Unpair."""

    def __init__(self):
        super().__init__()
        self.title("Jade Helper")

        self.label(self, "JADE HELPER", font=("Segoe UI", 18, "bold"), fg=ACCENT,
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
                         "Pause any time. Never touches J.A.D.E.'s memory\n"
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
        self.worker = HelperWorker(
            on_status=self._on_status,
            on_stats=self._on_stats,
        )
        self.worker.start()

    def _on_status(self, text):
        self.after(0, lambda: self.status_var.set(text.capitalize()))

    def _on_stats(self, stats):
        hours = stats["seconds_contributed"] / 3600
        text = (f"Jobs completed: {stats['jobs_completed']}   •   "
                f"Images rendered: {stats['images_rendered']}\n"
                f"Time contributed: {hours:.1f} h")
        self.after(0, lambda: self.stats_var.set(text))

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
        self.destroy()
        SetupWizard().mainloop()

    def _quit(self):
        if self.worker:
            self.worker.stop()
        self.destroy()


def run_gui():
    """Launch the GUI: setup wizard on first run, main window otherwise."""
    if load_identity():
        MainWindow().mainloop()
    else:
        SetupWizard().mainloop()
