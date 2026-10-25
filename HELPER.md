# Cyra Helper — Volunteer Guide

Everything you need to run a helper node, start to finish. If anything here doesn't work, open an issue on this repo and I'll fix it.

## 1. What you're running

A small background worker that renders images for Cyra when your PC is idle. It talks to the hub over a private Tailscale network. It only ever receives a render prompt and sends back a finished image — never Cyra's memory, conversations, tools, or files, and never your files.

## 2. Install Tailscale

Tailscale is a free private VPN — it's how your PC reaches the hub. Nothing about your normal internet changes.

1. Download it: https://tailscale.com/download (Windows)
2. Install it. You don't need to sign in yourself — the auth key in the next step handles that.

## 3. Where Skyler's auth key goes

The app asks for the Tailscale auth key on first run, in the setup window. Paste it into the **Tailscale auth key** field and hit **Connect**.

What the key does: it joins your PC to the project's Tailnet with a tag (`tag:helper`) that is only allowed to reach the hub's helper port — nothing else on the network. I generate the keys, so if you need one, ask me for it.

The key is used once during setup and is never stored or logged anywhere by the app.

## 4. Pairing / registration

There's no approval step and no account to make. When you hit **Connect**:

1. The app joins the Tailnet with your key.
2. It checks the hub is reachable.
3. It registers itself and gets a worker token automatically.
4. The main window opens and the worker starts.

From then on, launching the app goes straight to the main window. Pairing happens once.

Your pairing is stored at `%APPDATA%\CyraHelper\helper.json`. It holds your helper ID (`helper-` plus 12 hex characters), the worker token the hub issued, the hub address, and the helper name you chose. Three things are worth knowing about it:

- **The token is never printed or logged.** The app only ever sends it to the hub, in an `Authorization: Bearer` header.
- **The file is checked every time the app starts.** If it has been corrupted or hand-edited into a shape the app can't use — a token that isn't a plain urlsafe string, a hub address that isn't a valid `http://` or `https://` URL — the app treats the PC as unpaired and offers setup again, instead of failing silently forever.
- **Saving is crash-safe.** The app writes a temporary file first and then replaces the real one, so a crash or power cut mid-save can't leave a half-written pairing behind. **Unpair** (in the main window) deletes the pairing file and any leftover temporary copy, stops the worker, and returns you to setup.

## 5. GPU notes

Rendering happens on your GPU with Stable Diffusion (`runwayml/stable-diffusion-v1-5`, ~4GB one-time download — the app asks before downloading anything that big).

- **NVIDIA GPU:** install the CUDA build of torch for full speed:
  `pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126`
  (That CUDA path is for running from source, after the standard
  hash-verified install — `pip install --require-hashes -r
  requirements.lock.txt`. This one swap is the exception to
  hash-locking: a single package from PyTorch's own index, pinned to
  the same torch version the lock carries, chosen by the volunteer
  for their GPU.)
- **No NVIDIA GPU:** it still works on CPU — just much slower. The worker will only pick up jobs your machine can actually finish; CPU-only machines are welcome but expect long render times.
- The first render is slow (model load). After that the model stays in memory and jobs go faster.
- Render sizes are clamped by the helper and the hub alike: width and height 64–2048 (rounded down to a multiple of 8), steps 1–150.
- **Model safety:** a model the hub asks for must be a plain Hugging Face `owner/repo` ID — the app rejects local paths and anything that isn't that shape. Models load from safetensors weights only, and model-repository code is never executed (`trust_remote_code` is off). Approvals you give are remembered in `%APPDATA%\CyraHelper\known_models.json`, so you're only asked once per model.

## 6. Resource controls

- **Idle detection:** the worker only fetches jobs when your CPU is under the idle threshold (default 40%) and no fullscreen app is running (so it never steals your game).
- **Tune it:** console mode takes `--idle-cpu <percent>` — lower means pickier. Example: `CyraHelper.exe --console --idle-cpu 25`.
- **New models:** the hub can ask for a model you don't have yet. The app always asks before downloading (a yes/no dialog in the GUI, a prompt in console mode) and remembers each answer — every model is only asked about once. The prompt states the model's real size, looked up from Hugging Face before you're asked; if that lookup fails, the prompt says the size is unknown rather than guessing. Console mode can pre-approve with `--allow-model owner/repo` (repeatable).
- **Audit log (optional, off by default):** if you want a record of what your PC has rendered, set the `CYRA_HELPER_AUDIT_LOG` environment variable to `1` before launching (or pass `--audit-log` in console mode). Each rendered job is then written to `%APPDATA%\CyraHelper\audit_log.jsonl` — job ID, model, time, and the prompt — one line per job. The log stays on your PC; it is never sent anywhere. With the setting off, nothing is recorded.
- **Pause:** the Pause button (or Ctrl+C in console mode) stops new jobs immediately. The current render finishes first so nothing is lost; the hub just won't send more.
- **Stats:** the main window shows jobs completed, images rendered, and hours contributed.

## 7. Console / headless mode

If you'd rather run it with no window (e.g. on a spare rig):

```bat
CyraHelper.exe --console
```

First run still needs the GUI once to pair. Or pair unattended with a config file:

```bat
CyraHelper.exe --console --config helper-config.json
```

`helper-config.json`:
```json
{
  "hub_base": "http://<hub-address>:<port>",
  "name": "My Helper"
}
```

The config file must be a JSON object, and `hub_base` / `name` must be strings — a bad config is rejected with a message instead of crashing later. `--idle-cpu` must be between 0 and 100; out-of-range values are rejected at startup.

If the hub gives a terminal answer — `blocked`, a protocol mismatch, or a bad token — console mode exits instead of hanging, so a script or a spare rig doesn't sit there doing nothing. With no terminal attached, an unknown model download is refused outright: pre-approve it with `--allow-model owner/repo` (repeatable).

A `--console` debug twin, `CyraHelper-debug.exe`, ships next to the main exe and prints everything to a terminal — use it if something's acting up.

## 8. Troubleshooting

| Problem | What to do |
|---|---|
| "tailscale not found" | Install Tailscale (step 2), then restart the app. |
| "Hub not reachable" | Check the hub address. Make sure Tailscale shows as connected (look for the Tailscale icon in the system tray). |
| "protocol mismatch … update your Cyra Helper" | Your helper is out of date — grab the newest release. The hub and helper must speak the same protocol version. Verify the download first: each release ships a `SHA256SUMS-win64` file next to the zip — run `Get-FileHash <the-zip> -Algorithm SHA256` in PowerShell and check the hash matches before unzipping. |
| "blocked" | This machine's ID was blocklisted by the hub. If you think that's wrong, contact me. |
| "bad token — re-pair in the app" | The stored token stopped working. Unpair and run setup again. |
| Renders fail / out of memory | Your GPU ran out of VRAM. The worker reports the failure honestly and moves on — nothing breaks. A card with more VRAM helps. |
| App won't start at all | Run `CyraHelper-debug.exe` from a terminal and send me what it prints. |

## 9. Full uninstall

1. Close the app (right-click → close, or Ctrl+C in console mode).
2. Delete the app folder wherever you unzipped it.
3. Delete `%APPDATA%\CyraHelper` — that's your identity and token.
4. Optional: in the Tailscale app, remove the machine; optionally uninstall Tailscale.

No registry entries, no services, no leftovers.

---

## Can't volunteer compute? You can still help.

GPUs cost money, electricity costs money, hardware dies. If you want to back this but can't donate compute, a few sats keep the machines running:

```
bc1qvydns2fmyjzj55wzxq7ta28nxdhcmu9l9m6yg0
```

Every bit goes to the project. Thank you — seriously.
