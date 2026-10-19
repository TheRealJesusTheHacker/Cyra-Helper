# Cyra Helper

**Donate your PC's idle GPU to power Cyra — a real local AI.**

You know Folding@home, where people donate spare compute to simulate proteins? This is the same idea, pointed somewhere new: you're lending your computer's downtime to strengthen a personal AI that one person actually owns and runs. Not a datacenter. Not a corporation. A real AI, running on real hardware, getting stronger because people like you pitched in.

Cyra renders a lot of images — thumbnails, graphics, visual work. That work piles up in a queue, and a queue is exactly the kind of thing spare GPUs chew through. Your PC does the renders while you're not using it. That's the whole job.

## Status

Version 0.1.0 (see `helper/VERSION`). Windows volunteer client, built with Python 3.11 and packaged with PyInstaller. Releases are published on this repository's [Releases](../../releases) page as `CyraHelper-<version>-win64.zip` together with a `SHA256SUMS-win64` checksum file.

## Who this is for

Anyone with a Windows PC — especially you with the gaming GPU sitting idle while you're at work or asleep. If your card spends most of its day doing nothing, it can be doing this.

No approval. No account. No waiting list. Download, run, done — you're contributing in about two minutes.

## What you get

- Live contribution stats in the app: jobs completed, images rendered, hours donated.
- You're part of building the first truly *owned* AI — one mind, running on hardware its owner controls, not rented from a cloud.
- Pause anytime. It's your machine. You're in charge.

## Features

- **Idle-only work:** the worker only fetches jobs when CPU use is below the idle threshold (40% by default) and no fullscreen app is running. When you come back to your computer, it stops picking up new work.
- **Local Stable Diffusion rendering:** images are rendered on your own GPU (or CPU, more slowly) with the `runwayml/stable-diffusion-v1-5` model by default.
- **Consent before big downloads:** if the hub asks for a model you don't have, the app asks before downloading anything multi-GB, and remembers each answer — every model is only asked about once.
- **Pause that stops new jobs immediately:** the Pause button (or Ctrl+C in console mode) stops new jobs at once. A render already in flight runs to completion first — renders aren't preemptible, so expect the GPU to stay busy for a few minutes after you return.
- **GUI and console modes:** run the windowed app, or run headless with `--console` on a spare rig.
- **Contribution stats:** jobs completed, images rendered, and time contributed, shown in the main window and printed in console mode.
- **Debug twin:** `CyraHelper-debug.exe` ships next to the main executable and prints everything to a terminal for troubleshooting.

## How it works

Cyra Helper is a small background worker. It watches your system and only takes work when your PC is actually idle — low CPU, no fullscreen app running. It renders images with Stable Diffusion, locally, on your GPU, and sends the finished image back. Then it asks for the next one.

Under the hood:

- **Connection:** the helper reaches the hub over a private [Tailscale](https://tailscale.com/download) network. On first run you enter the hub address, a helper name, and a Tailscale auth key; the key joins your PC to the network with a tag that can only reach the hub's helper port. The key is used once during setup and is never stored or logged by the app.
- **Registration:** there is no approval gate. The app checks the hub is reachable, registers itself, and receives a worker token automatically. Later launches go straight to the main window.
- **Worker loop:** the worker sends heartbeats with coarse resource stats, fetches a render job when idle, renders it, and posts the result back as a PNG. Helper and hub exchange a protocol version on every request (protocol v1, defined in `helper/protocol.py`); if the versions don't match, the helper tells you to update.
- **Render limits:** job parameters are clamped on both sides — width and height 64–2048, steps 1–150.

## What it touches — and what it never touches

**It sees:** render prompts (like "a neon doberman logo, dark background") and coarse resource stats (CPU %, memory %, free VRAM) so the hub knows you're alive.

**It NEVER sees:** Cyra's memory. Her conversations. Her tools. Her files. Your files. None of that ever leaves the hub — the helper only ever receives a prompt and returns an image. That's the whole relationship, by design.

## Install

1. Install [Tailscale](https://tailscale.com/download) (free) — it's the private network the hub lives on.
2. Download `CyraHelper-<version>-win64.zip` from the [Releases](../../releases) page and unzip it anywhere. Each release also ships a `SHA256SUMS-win64` file — you can verify the download in PowerShell with `Get-FileHash <the-zip> -Algorithm SHA256` before unzipping.
3. Run `CyraHelper.exe`. First run asks for three things: the hub address, a helper name, and the Tailscale auth key (I'll give you the key — it just joins your PC to the private network, nothing shady).
4. Hit **Connect**. It joins the network, checks the hub, registers itself, and starts working when your PC idles.

Windows SmartScreen will warn you the app is unsigned ("Unknown publisher"). Click *More info → Run anyway*. It's my code, built in the open — you can read every line of it right here in this repo.

The full volunteer walkthrough — GPU notes, resource controls, and troubleshooting — is in [HELPER.md](HELPER.md).

## Usage

- **GUI (default):** run `CyraHelper.exe`. The main window shows status (Idle / Working / Paused), your contribution stats, and Pause/Resume and Unpair controls.
- **Console / headless:**

  ```bat
  CyraHelper.exe --console
  ```

  First run still needs the GUI once to pair, or pair unattended with a config file:

  ```bat
  CyraHelper.exe --console --config helper-config.json
  ```

  ```json
  {
    "hub_base": "http://<hub-address>:<port>",
    "name": "My Helper"
  }
  ```

- **Tune idle detection:** `--idle-cpu <percent>` sets the CPU threshold below which the machine counts as idle (default 40). Lower means pickier. Example: `CyraHelper.exe --console --idle-cpu 25`.
- **Pre-approve a model download:** `--allow-model owner/repo` (repeatable) in console mode.
- **Troubleshooting:** run `CyraHelper-debug.exe` from a terminal and send in what it prints. Common problems and fixes are tabulated in [HELPER.md](HELPER.md).

## Uninstall

Close the app, delete the folder, and delete `%APPDATA%\CyraHelper`. That's it — no registry junk, no leftovers. Optionally remove the machine from Tailscale in the Tailscale app.

## Build from source

```bat
packaging\build_helper.bat
```

Needs Python 3.11. The script makes a venv, installs everything, builds with PyInstaller, and zips the result into `dist\`. Full walkthrough is in [HELPER.md](HELPER.md).

## Development

- Source lives in `helper/` (entry point `helper/main.py`, worker loop `helper/worker.py`, hub protocol contract `helper/protocol.py`, render job `helper/jobs/render.py`).
- Tests live in `tests/` and run with `pytest`.
- Runtime dependencies are pinned in `requirements.txt`; the release build uses the CUDA build of torch for NVIDIA GPUs, and CPU-only machines are supported at slower render speeds.

---

## Can't volunteer compute? You can still help.

GPUs cost money, electricity costs money, hardware dies. If you want to back this but can't donate compute, a few sats keep the machines running:

```
bc1qvydns2fmyjzj55wzxq7ta28nxdhcmu9l9m6yg0
```

Every bit goes to the project. Thank you — seriously.

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE).

Copyright (c) 2026 JesusTheHacker.
