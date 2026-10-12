# Jade Helper — Volunteer Guide

Everything you need to run a helper node, start to finish. If anything here doesn't work, open an issue on this repo and I'll fix it.

## 1. What you're running

A small background worker that renders images for J.A.D.E. when your PC is idle. It talks to the hub over a private Tailscale network. It only ever receives a render prompt and sends back a finished image — never J.A.D.E.'s memory, conversations, tools, or files, and never your files.

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

## 5. GPU notes

Rendering happens on your GPU with Stable Diffusion (`runwayml/stable-diffusion-v1-5`, ~4GB one-time download — the app asks before downloading anything that big).

- **NVIDIA GPU:** install the CUDA build of torch for full speed:
  `pip install torch --index-url https://download.pytorch.org/whl/cu121`
  (The prebuilt `JadeHelper.exe` already ships with CUDA torch.)
- **No NVIDIA GPU:** it still works on CPU — just much slower. The worker will only pick up jobs your machine can actually finish; CPU-only machines are welcome but expect long render times.
- The first render is slow (model load). After that the model stays in memory and jobs go faster.

## 6. Resource controls

- **Idle detection:** the worker only fetches jobs when your CPU is under the idle threshold (default 40%) and no fullscreen app is running (so it never steals your game).
- **Tune it:** console mode takes `--idle-cpu <percent>` — lower means pickier. Example: `JadeHelper.exe --console --idle-cpu 25`.
- **New models:** the hub can ask for a model you don't have yet. The app always asks before downloading anything multi-GB (a yes/no dialog in the GUI, a prompt in console mode) and remembers each answer — every model is only asked about once. Console mode can pre-approve with `--allow-model owner/repo` (repeatable).
- **Pause:** the Pause button (or Ctrl+C in console mode) stops new jobs immediately. The current render finishes first so nothing is lost; the hub just won't send more.
- **Stats:** the main window shows jobs completed, images rendered, and hours contributed.

## 7. Console / headless mode

If you'd rather run it with no window (e.g. on a spare rig):

```bat
JadeHelper.exe --console
```

First run still needs the GUI once to pair. Or pair unattended with a config file:

```bat
JadeHelper.exe --console --config helper-config.json
```

`helper-config.json`:
```json
{
  "hub_base": "http://<hub-address>:<port>",
  "name": "My Helper"
}
```

A `--console` debug twin, `JadeHelper-debug.exe`, ships next to the main exe and prints everything to a terminal — use it if something's acting up.

## 8. Troubleshooting

| Problem | What to do |
|---|---|
| "tailscale not found" | Install Tailscale (step 2), then restart the app. |
| "Hub not reachable" | Check the hub address. Make sure Tailscale shows as connected (look for the Tailscale icon in the system tray). |
| "protocol mismatch … update your Jade Helper" | Your helper is out of date — grab the newest release. The hub and helper must speak the same protocol version. |
| "blocked" | This machine's ID was blocklisted by the hub. If you think that's wrong, contact me. |
| "bad token — re-pair in the app" | The stored token stopped working. Unpair and run setup again. |
| Renders fail / out of memory | Your GPU ran out of VRAM. The worker reports the failure honestly and moves on — nothing breaks. A card with more VRAM helps. |
| App won't start at all | Run `JadeHelper-debug.exe` from a terminal and send me what it prints. |

## 9. Full uninstall

1. Close the app (right-click → close, or Ctrl+C in console mode).
2. Delete the app folder wherever you unzipped it.
3. Delete `%APPDATA%\JadeHelper` — that's your identity and token.
4. Optional: in the Tailscale app, remove the machine; optionally uninstall Tailscale.

No registry entries, no services, no leftovers.

---

## Can't volunteer compute? You can still help.

GPUs cost money, electricity costs money, hardware dies. If you want to back this but can't donate compute, a few sats keep the machines running:

```
bc1qvydns2fmyjzj55wzxq7ta28nxdhcmu9l9m6yg0
```

Every bit goes to the project. Thank you — seriously.
