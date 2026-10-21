# Cyra Helper

**Donate your Windows PC's idle GPU to power Cyra — a real, personally owned local AI.**

[![Release build](https://github.com/TheRealJesusTheHacker/Cyra-Helper/actions/workflows/release.yml/badge.svg?style=flat-square)](https://github.com/TheRealJesusTheHacker/Cyra-Helper/actions/workflows/release.yml)
[![Version](https://img.shields.io/badge/version-0.1.0-blue?style=flat-square)](helper/VERSION)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11-blue?style=flat-square)](requirements.txt)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey?style=flat-square)](https://github.com/TheRealJesusTheHacker/Cyra-Helper/releases)

Cyra Helper is the volunteer compute client for Cyra. You know Folding@home, where people donate spare compute to simulate proteins? This is the same idea, pointed somewhere new: you lend your computer's downtime to strengthen a personal AI that one person actually owns and runs. Not a datacenter. Not a corporation. A real AI, running on real hardware, getting stronger because people like you pitched in.

> The full start-to-finish volunteer walkthrough — install, pairing, GPU notes, resource controls, and troubleshooting — is in [HELPER.md](HELPER.md). This README is the complete reference for what the helper is, how it works, and how it is built.

## Overview

Cyra renders a lot of images — thumbnails, graphics, and other visual work. That work piles up in a queue on Cyra's hub, and a queue is exactly the kind of thing spare GPUs chew through. Cyra Helper is the other half of that arrangement:

- **The hub** (on the owner's side, not in this repository) holds the render queue and hands out one job at a time.
- **The helper** (this repository) runs on a volunteer's Windows PC, fetches a job only when the PC is idle, renders the image locally with Stable Diffusion, and posts the finished PNG back to the hub.

That is the whole job. The helper receives a render prompt and returns an image. It never receives Cyra's memory, conversations, tools, or files — see [What it touches — and what it never touches](#what-it-touches--and-what-it-never-touches).

### Status

- **Version:** 0.1.0 — the version source is [`helper/VERSION`](helper/VERSION).
- **Latest release:** `v0.1.0`, published on this repository's [Releases](https://github.com/TheRealJesusTheHacker/Cyra-Helper/releases) page as `CyraHelper-0.1.0-win64.zip` together with a `SHA256SUMS-win64` checksum file.
- **Platform:** Windows. The client is written in Python 3.11 and packaged with PyInstaller as a one-directory bundle.
- **Protocol:** helper↔hub protocol v1, defined in [`helper/protocol.py`](helper/protocol.py). Helper and hub exchange the protocol version on every request; a mismatch stops the helper and tells the volunteer to update.

### Who this is for

Anyone with a Windows PC — especially a gaming GPU sitting idle while its owner is at work or asleep. If your card spends most of its day doing nothing, it can be doing this instead.

- No approval gate, no account, and no waiting list. Download, pair once, and you are contributing.
- An NVIDIA GPU renders at full speed with the CUDA build of PyTorch. A machine without one still works on CPU — noticeably more slowly, and the first render on any machine is slow while the model loads.
- No approval does not mean no control: the hub keeps a blocklist keyed by helper ID and can block or revoke a misbehaving helper, and a blocked helper stops with a clear `blocked` status.

## Features

- **Idle-only work.** The worker only fetches a job when CPU use is below the idle threshold (40% by default) and no fullscreen application is running. When you come back to your computer, it stops picking up new work.
- **Local Stable Diffusion rendering.** Images are rendered on your own GPU (or CPU) with the `runwayml/stable-diffusion-v1-5` model by default. The pipeline loads lazily on the first job and stays in memory for the rest of the session.
- **Consent before big downloads — with the real size.** If the hub asks for a model you do not have, the app first looks up the model's real size from Hugging Face hub metadata and states that figure in the consent prompt; if the lookup fails, the prompt says the size is unknown instead of quoting a vague figure. The app asks before downloading and remembers each answer, so every model is only asked about once. Approvals persist in `%APPDATA%\CyraHelper\known_models.json`; console mode can also pre-approve models with `--allow-model`.
- **Optional local audit log (opt-in, off by default).** Turn it on (`CYRA_HELPER_AUDIT_LOG=1`, or `--audit-log` in console mode) and every job your PC renders is recorded locally — job ID, model, timestamp, and prompt — in `%APPDATA%\CyraHelper\audit_log.jsonl`, so you can see exactly what your machine was asked to render. The log never leaves your PC; with the setting off, nothing is written.
- **Safe model loading.** A hub-supplied model ID must be a plain Hugging Face `owner/repo` identifier — local paths and `.` / `..` path segments are rejected. Models load with safetensors weights only (`use_safetensors=True`) and remote code execution disabled (`trust_remote_code=False`).
- **Pause that stops new jobs immediately.** The Pause button (or Ctrl+C in console mode) stops new jobs at once. A render already in flight runs to completion first — renders are not preemptible, so expect the GPU to stay busy for a few minutes after you return.
- **GUI and console modes.** Run the windowed tkinter app, or run headless with `--console` on a spare rig. A debug twin, `CyraHelper-debug.exe`, ships next to the main executable and prints everything to a terminal for troubleshooting.
- **Contribution stats.** Jobs completed, images rendered, and time contributed — shown in the main window and printed in console mode.
- **Heartbeats that survive long renders.** The worker heartbeats every 30 seconds, and it keeps sending busy heartbeats while a render runs, so a long job does not make a healthy helper look dead to the hub.
- **Honest failure reporting.** Unsupported job types, malformed hub payloads, jobs without a usable ID, and render errors are reported back or surfaced as a status — they do not silently kill the worker loop. Results are posted with a longer timeout and one retry, because the result is the whole point of the job.
- **Privacy scrubbing on the way out.** Error text sent to the hub is scrubbed of local path prefixes (`%USERPROFILE%`, `%APPDATA%`, `%LOCALAPPDATA%`, and the home directory) so a volunteer's Windows username does not leak inside a file path, and hub-supplied error strings are capped before they reach a status line or dialog.

## How it works

```text
Volunteer PC                                        Cyra hub
┌─────────────────────────────┐                    ┌──────────────────┐
│ Cyra Helper                 │   Tailscale only   │  Render queue    │
│                             │ ◄────────────────► │  /helper/v1/*    │
│  1. Pair once (see below)   │                    │                  │
│  2. Heartbeat every 30 s    │                    └──────────────────┘
│  3. If idle: fetch one job  │
│  4. Render locally (GPU/CPU)│
│  5. Post the PNG result     │
└─────────────────────────────┘
```

### Connection

The helper reaches the hub over a private [Tailscale](https://tailscale.com/download) network; the hub is not exposed to the public internet for this traffic. On first run you enter the hub address, a helper name, and a Tailscale auth key supplied by the project owner. The key joins your PC to the network with the `tag:helper` tag, which is only allowed to reach the hub's helper port — no other host and no other port.

The auth key is handled narrowly, by design in [`helper/tailscale.py`](helper/tailscale.py):

- It is passed to `tailscale up` through the `TS_AUTHKEY` environment variable, never as a command-line argument, so it does not appear in process listings.
- It is used once during setup and is never stored or logged by the app.

### Pairing and the hub token

There is no approval gate. When you hit **Connect** in the setup wizard, the app:

1. Joins the Tailnet with your auth key.
2. Checks the hub is reachable with a TCP connect test.
3. Registers itself and receives a worker token automatically.
4. Opens the main window and starts the worker.

Later launches go straight to the main window — pairing happens once. Registration (`POST /helper/v1/register`) sends the protocol version, a freshly minted helper ID (`helper-` plus 12 lowercase hex characters), the helper name (1–64 characters, no control characters), the platform (`windows`), and a capabilities block probed from the machine: CPU count, RAM in GB, GPU name, VRAM in GB, and whether CUDA is available.

The returned identity is stored at `%APPDATA%\CyraHelper\helper.json`:

```json
{
  "body_id": "helper-<12 hex chars>",
  "token": "<urlsafe worker token>",
  "hub_base": "http://<hub-address>:<port>",
  "name": "My Helper",
  "hub_id": "<informational hub identifier>"
}
```

Token and identity handling was hardened across the 2026-10-13 and 2026-10-19 sweeps, and the current behavior in [`helper/registration.py`](helper/registration.py) is:

- **The token is never logged, printed, or put in an exception message.** Authenticated requests send it only as an `Authorization: Bearer <token>` header.
- **Stored identities are validated on load, not trusted.** The helper ID must match its pattern exactly, the token must be a urlsafe string of 1–512 characters, and `hub_base` must be a parseable `http://` or `https://` URL with a real host, a valid port when present, and no whitespace or control characters. A corrupt or hand-edited identity is treated as “not paired” and the app offers re-pairing, instead of wedging forever on failed heartbeats.
- **Registration responses are validated the same way.** A 200 response without a usable token fails at registration with a clear message, a non-JSON response fails with a clear message instead of a parser error, and a non-string `hub_id` is dropped rather than persisted.
- **The identity file is written atomically.** The app writes a temporary file, restricts it (best-effort `0600`; on Windows `os.chmod` only honors the read-only bit, so this is defense-in-depth, not a guarantee), then replaces the live file — a crash mid-write cannot leave a torn identity behind. **Unpair** from the main window deletes both the identity file and any leftover temporary copy, stops the worker, and returns to the setup wizard.

Terminal hub answers stop the worker with a status the volunteer can act on: `400` is a protocol mismatch (“update your Cyra Helper”), `401` is a bad token (“re-pair in the app”), and `403` is `blocked`.

### Worker loop

The worker in [`helper/worker.py`](helper/worker.py) runs in a daemon thread:

1. Load the stored identity, registering first if there is none.
2. Every 30 seconds, POST a heartbeat with coarse resource stats: CPU %, memory %, free GPU VRAM in GB, and `busy` / `paused` flags.
3. If the helper is not paused, not busy, and the system is idle (CPU below the threshold and no fullscreen app, checked best-effort via the Windows foreground window), fetch one job.
4. A job without a valid `job_id` is refused up front, and any job type other than `media.render` is reported back as unsupported instead of being attempted.
5. The render runs on its own daemon thread while the worker keeps sending busy heartbeats every 30 seconds. Stopping the app mid-render does not block shutdown on the GPU — the unfinished result is dropped and the hub times the job out, the same net effect as closing the app mid-render.
6. On success, the PNG is base64-encoded and posted to the result endpoint; on failure, a scrubbed, length-capped error is posted instead. Either way the loop continues — a failed job never kills the worker.

### Rendering

Render jobs are handled by [`helper/jobs/render.py`](helper/jobs/render.py):

- **Parameters:** `prompt` (required, must be a non-empty string), `negative_prompt`, `width`, `height`, `steps`, `seed` (integer or null), and `model`.
- **Clamps, mirrored on both sides:** width and height are clamped to 64–2048 and then rounded down to a multiple of 8 (the diffusion latent space is ÷8); steps are clamped to 1–150. Defaults are 512×512 and 30 steps. Out-of-range and non-numeric values are clamped rather than crashing the render.
- **Pipeline caching:** the pipeline for a model is loaded once and kept in memory. Loads are serialized with a lock so two threads can never load the same pipeline twice and leak VRAM. A switch loads the new pipeline before releasing the old one. **Decision (closed 2026-10-21): keep load-new-before-releasing-old** — a failed new load must never leave the volunteer with no pipeline; the accepted cost is peak double VRAM during a switch.
- **Output:** the finished image is encoded as PNG with fast compression (level 1) — PNG is lossless at every level, so only the encode time changes, not the pixels.

### Protocol

The contract lives in [`helper/protocol.py`](helper/protocol.py) and both sides must match it. Base path: `/helper/v1`. All requests and responses are JSON, and every body carries `protocol_v: 1`.

| Endpoint | Request (key fields) | Success response |
|---|---|---|
| `POST /helper/v1/register` | `body_id`, `name`, `platform`, `capabilities` | `{ok, token, hub_id, poll_secs}` |
| `POST /helper/v1/heartbeat` | `body_id`, `cpu_pct`, `mem_pct`, `gpu_free_vram_gb`, `busy`, `paused` | `{ok}` |
| `POST /helper/v1/job/fetch` | `body_id` | `{ok, job}` — `job` is `null` when the queue is empty |
| `POST /helper/v1/job/result` | `body_id`, `job_id`, `ok`, `image_b64`, `seconds`, `error` | `{ok, accepted, reason}` |

A `media.render` job carries `job_id`, `type: "media.render"`, and the render `params` described above. `image_b64` holds the PNG bytes, base64-encoded, when the render succeeded.

## What it touches — and what it never touches

**It sees:** render prompts (like “a neon doberman logo, dark background”) and coarse resource stats (CPU %, memory %, free VRAM) so the hub knows you are alive and what your machine can handle.

**It never sees:** Cyra's memory. Her conversations. Her tools. Her files. Your files. None of that ever leaves the hub — the helper only ever receives a prompt and returns an image. That is the whole relationship, by design.

## Install

You need a Windows PC and [Tailscale](https://tailscale.com/download) (free) — it is the private network the hub lives on.

1. Install Tailscale. You do not need to sign in yourself — the auth key in step 3 handles joining.
2. Download `CyraHelper-<version>-win64.zip` from the [Releases](https://github.com/TheRealJesusTheHacker/Cyra-Helper/releases) page and unzip it anywhere. Each release also ships a `SHA256SUMS-win64` file — verify the download in PowerShell before unzipping:

   ```powershell
   Get-FileHash .\CyraHelper-<version>-win64.zip -Algorithm SHA256
   ```

   Compare the printed hash with the one in `SHA256SUMS-win64`.
3. Run `CyraHelper.exe`. First run asks for three things: the hub address, a helper name, and the Tailscale auth key from the project owner.
4. Hit **Connect**. The app joins the network, checks the hub, registers itself, and starts working when your PC idles.

Windows SmartScreen will warn you the app is unsigned (“Unknown publisher”). Click *More info → Run anyway*. The code is open in this repository — you can read every line before you run it.

The full volunteer walkthrough — GPU notes, resource controls, and troubleshooting — is in [HELPER.md](HELPER.md).

## Usage

### GUI (default)

Run `CyraHelper.exe`. On first run you get the setup wizard; afterwards the main window shows the status (Idle / Working / Paused), your contribution stats (jobs completed, images rendered, time contributed), and **Pause/Resume** and **Unpair** controls.

### Console / headless

```bat
CyraHelper.exe --console
```

First run still needs the GUI once to pair — or pair unattended with a config file:

```bat
CyraHelper.exe --console --config helper-config.json
```

```json
{
  "hub_base": "http://<hub-address>:<port>",
  "name": "My Helper"
}
```

The config file must be a JSON object, and `hub_base` / `name` must be strings when present — an invalid config is rejected with a message instead of crashing later. In console mode, terminal statuses (`blocked`, protocol mismatch, bad token, registration failure) exit the process cleanly instead of hanging, and Ctrl+C / SIGTERM shut the worker down.

Model consent in console mode: an interactive terminal is asked `Allow it? [y/N]` before a new model downloads. With no terminal attached, an unknown model is refused outright — pre-approve it explicitly with `--allow-model` (below).

### Command-line options

These options are defined by the entry point in [`helper/main.py`](helper/main.py) and apply to the packaged executable and to running from source.

| Option | Default | What it does |
|---|---|---|
| `--console` | off (GUI) | Run the headless worker with no window. |
| `--config <path>` | — | JSON file with `hub_base` and `name` for unattended first registration in console mode. |
| `--idle-cpu <percent>` | `40` | CPU % below which the machine counts as idle. Must be between 0 and 100 — out-of-range values are rejected instead of silently disabling or forcing the idle gate. Lower means pickier. Example: `CyraHelper.exe --console --idle-cpu 25`. |
| `--allow-model <owner/repo>` | — | Pre-approve a Hugging Face model for download with no prompt. Repeatable. The default model is always allowed. |
| `--audit-log` | off | Console mode: keep a local log of rendered prompts (job ID, model, time, prompt) at `%APPDATA%\CyraHelper\audit_log.jsonl`. The log never leaves the PC. GUI launches can opt in with the `CYRA_HELPER_AUDIT_LOG=1` environment variable instead. |

### Troubleshooting

Run `CyraHelper-debug.exe` from a terminal and send in what it prints. Common problems and fixes — unreachable hub, protocol mismatch, blocked, bad token, out-of-VRAM renders — are tabulated in [HELPER.md](HELPER.md#8-troubleshooting).

## Configuration

Cyra Helper has no central settings file. Its one environment-variable setting is the opt-in audit log (below); the rest of its state is deliberately small:

| What | Where | Notes |
|---|---|---|
| Identity and worker token | `%APPDATA%\CyraHelper\helper.json` | Written at pairing; validated on every load; deleted by Unpair. See [Pairing and the hub token](#pairing-and-the-hub-token). |
| Approved models | `%APPDATA%\CyraHelper\known_models.json` | The set of model IDs you have approved, so each model is only asked about once. Written atomically, like the identity file. |
| Unattended pairing | A JSON file you pass with `--config` | Only `hub_base` and `name`; used once for a console-mode first registration. |
| Idle threshold / pre-approved models | `--idle-cpu` / `--allow-model` flags | Per-launch settings; nothing is persisted for them. |
| Prompt audit log (opt-in) | `%APPDATA%\CyraHelper\audit_log.jsonl` | Off by default. Enable with `CYRA_HELPER_AUDIT_LOG=1` or `--audit-log` (console). One JSON line per rendered job: job ID, model, timestamp, prompt. Local only — never sent to the hub or anywhere else. |

Deleting `%APPDATA%\CyraHelper` removes all of the app's local state.

## Uninstall

Close the app, delete the folder you unzipped, and delete `%APPDATA%\CyraHelper`. That is it — no registry entries, no services, no leftovers. Optionally remove the machine from Tailscale in the Tailscale app, and uninstall Tailscale itself if you do not use it for anything else.

## Build from source

```bat
packaging\build_helper.bat
```

You need Python 3.11 on Windows. The script creates `.venv` if needed, installs `requirements.txt`, installs PyInstaller, builds with [`packaging/cyra-helper.spec`](packaging/cyra-helper.spec), checks that `dist\CyraHelper\CyraHelper.exe` exists, and zips the bundle to `dist\CyraHelper-<version>-win64.zip`, taking the version from `helper/VERSION`.

Packaging notes, from the spec file:

- **One-directory, not one-file.** Torch and diffusers ship large native DLLs; a one-directory bundle keeps them as real files the loader can find instead of unpacking a one-file bundle into a temp directory on every launch.
- **Two executables ship together:** `CyraHelper.exe` (windowed) and `CyraHelper-debug.exe` (console twin for crash logging and troubleshooting).
- **Unsigned.** Local and release builds are unsigned, so SmartScreen warns on first run (see [Install](#install)).

Releases are built by [`.github/workflows/release.yml`](.github/workflows/release.yml) on `windows-latest`: pushing a `v*` tag stamps the version from `helper/VERSION`, installs [`packaging/requirements-ci.txt`](packaging/requirements-ci.txt) (CPU torch — the runner only needs a buildable import, not a GPU), builds with PyInstaller, and publishes the zip plus `SHA256SUMS-win64` straight to the GitHub Release. A manual `workflow_dispatch` run builds only, with no upload. Dependabot is configured in [`.github/dependabot.yml`](.github/dependabot.yml) for weekly pip and GitHub Actions update proposals.

## Development

### Repository layout

| Path | What it is |
|---|---|
| `helper/main.py` | Entry point: argument parsing, console mode, config-file validation. |
| `helper/gui.py` | tkinter GUI: first-run setup wizard and main window. |
| `helper/worker.py` | Background worker: heartbeat, idle detection, job fetch, render dispatch, result reporting. |
| `helper/registration.py` | Identity storage and validation, capability probing, hub registration. |
| `helper/protocol.py` | The helper↔hub protocol v1 contract — endpoint paths and payload shapes. |
| `helper/tailscale.py` | `tailscale up` with the auth key in the environment, and the hub reachability check. |
| `helper/jobs/render.py` | Local Stable Diffusion rendering: consent gate, model safety checks, clamps, pipeline cache. |
| `helper/VERSION` | The single version source for the app and the release build. |
| `tests/` | The pytest suite (see below). |
| `packaging/` | PyInstaller spec, local build script, and CI requirements. |
| `HELPER.md` | The volunteer guide: install, pairing, GPU notes, and troubleshooting. |

### Dependencies

Runtime dependencies are pinned in [`requirements.txt`](requirements.txt) to the exact versions the v0.1.0 release build was verified against: `requests`, `psutil`, `Pillow`, `diffusers`, `accelerate`, `safetensors`, and `huggingface_hub`, plus `torch`. Torch is the one install choice: use the CUDA build for an NVIDIA GPU or the CPU build otherwise — the commands for both are commented in `requirements.txt`. The release workflow's CI requirements ([`packaging/requirements-ci.txt`](packaging/requirements-ci.txt)) use CPU torch and add PyInstaller.

### Testing

Tests live in `tests/` and run with `pytest` from the repository root:

```bat
python -m pytest
python -m pytest --collect-only
```

Verified against a fresh snapshot of this repository at version 0.1.0: **127 tests collected, and all 127 pass.** The suite covers the protocol contract and heartbeat shape, identity load/save validation (including corrupt tokens, hub URLs, and non-JSON hub responses), the model consent gate and its persistence, the consent prompt's real-size wording (and its "size is unknown" fallback), the opt-in audit log's off-by-default and enabled behaviour, render clamps and model-ID rejection, malformed hub payloads, heartbeats during long renders, stopping mid-render, and worker restart after stop. [`tests/conftest.py`](tests/conftest.py) puts the repository root on `sys.path` and installs a minimal `psutil` stub when the real package is unavailable, so a bare `pytest` run works on a plain development machine.

## Project history

This repository was formerly named **Jade-Helper**. It was renamed to **Cyra-Helper** on 2026-10-18 as part of the J.A.D.E. → Cyra rename; the product name is Cyra Helper everywhere.

---

## Can't volunteer compute? You can still help.

GPUs cost money, electricity costs money, hardware dies. If you want to back this but can't donate compute, a few sats keep the machines running:

```text
bc1qvydns2fmyjzj55wzxq7ta28nxdhcmu9l9m6yg0
```

Every bit goes to the project. Thank you — seriously.

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE).

Copyright (c) 2026 JesusTheHacker.
