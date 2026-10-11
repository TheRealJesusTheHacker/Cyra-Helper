# Jade Helper

**Donate your PC's idle GPU to power J.A.D.E. — a real local AI.**

You know Folding@home, where people donate spare compute to simulate proteins? This is the same idea, pointed somewhere new: you're lending your computer's downtime to strengthen a personal AI that one person actually owns and runs. Not a datacenter. Not a corporation. A real AI, running on real hardware, getting stronger because people like you pitched in.

J.A.D.E. renders a lot of images — thumbnails, graphics, visual work. That work piles up in a queue, and a queue is exactly the kind of thing spare GPUs chew through. Your PC does the renders while you're not using it. That's the whole job.

## Who this is for

Anyone with a Windows PC — especially you with the gaming GPU sitting idle while you're at work or asleep. If your card spends most of its day doing nothing, it can be doing this.

No approval. No account. No waiting list. Download, run, done — you're contributing in about two minutes.

## What you get

- Live contribution stats in the app: jobs completed, images rendered, hours donated.
- You're part of building the first truly *owned* AI — one mind, running on hardware its owner controls, not rented from a cloud.
- Pause anytime. It's your machine. You're in charge.

## How it works

Jade Helper is a small background worker. It watches your system and only takes work when your PC is actually idle — low CPU, no fullscreen app running. The moment you come back to your computer, it backs off. You set how aggressive it is, and the Pause button works instantly, no questions asked.

It renders images with Stable Diffusion, locally, on your GPU, and sends the finished image back. Then it asks for the next one.

## What it touches — and what it never touches

**It sees:** render prompts (like "a neon doberman logo, dark background") and coarse resource stats (CPU %, memory %, free VRAM) so the hub knows you're alive.

**It NEVER sees:** J.A.D.E.'s memory. Her conversations. Her tools. Her files. Your files. None of that ever leaves the hub — the helper only ever receives a prompt and returns an image. That's the whole relationship, by design.

## Install

1. Install [Tailscale](https://tailscale.com/download) (free) — it's the private network the hub lives on.
2. Download `JadeHelper-<version>-win64.zip` from the [Releases](../../releases) page and unzip it anywhere.
3. Run `JadeHelper.exe`. First run asks for three things: the hub address, a helper name, and the Tailscale auth key (I'll give you the key — it just joins your PC to the private network, nothing shady).
4. Hit **Connect**. It joins the network, checks the hub, registers itself, and starts working when your PC idles.

Windows SmartScreen will warn you the app is unsigned ("Unknown publisher"). Click *More info → Run anyway*. It's my code, built in the open — you can read every line of it right here in this repo.

## Uninstall

Close the app, delete the folder, and delete `%APPDATA%\JadeHelper`. That's it — no registry junk, no leftovers. Optionally remove the machine from Tailscale in the Tailscale app.

## Build from source

```bat
packaging\build_helper.bat
```

Needs Python 3.11. The script makes a venv, installs everything, builds with PyInstaller, and zips the result into `dist\`. Full walkthrough is in [HELPER.md](HELPER.md).

---

## Can't volunteer compute? You can still help.

GPUs cost money, electricity costs money, hardware dies. If you want to back this but can't donate compute, a few sats keep the machines running:

```
bc1qvydns2fmyjzj55wzxq7ta28nxdhcmu9l9m6yg0
```

Every bit goes to the project. Thank you — seriously.
