# Livestream quickstart

A local-first control room for rehearsing an AI livestream **before an audience sees it**. Python runs the worker; your browser is the dashboard. No Render account, X credentials, or GPU is needed for the offline test.

Local preview is the default. **Send to X** separately arms an RTMPS relay; saving keys or starting preview never publishes. The Twitch starter is unchanged.

## Run locally

Requires macOS or Linux, Python 3.12+, and FFmpeg on PATH. Windows support is not implemented (the worker uses POSIX signals and inherited pipes).

```sh
# From this repository:
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-local.txt
python run.py
```

Install FFmpeg if needed: `brew install ffmpeg` on macOS, or `sudo apt install ffmpeg` on Ubuntu. Alternatively, with uv: `uv venv --python 3.12` then `uv pip install -r requirements-local.txt`.

The browser opens at **http://127.0.0.1:8091**. Use `python run.py --no-browser` to suppress opening it, or `--port 8092` to choose another port.

1. Choose **Test signal** in the Model selector and click **Start preview**.
2. Within a few seconds, the locally encoded synthetic video appears. This is a codec/playback test, not H3 output.
3. Click **Stop** when you are finished. Sessions run continuously until stopped; closing the browser does not stop generation.

Preview uses ordinary H.264/AAC MP4 clips in two-second segments; small transitions between clips are expected. It is a buffered inspection player, not yet seamless broadcast monitoring. Use Stop or Ctrl+C to stop Python's worker. The recent 60 clips (approximately two minutes) form a rolling buffer: older completed preview clips are automatically deleted, including when the browser is closed. This is a preview, not an archival recording. Timed checks remain available through the local API with an explicit duration of 5–300 seconds; omitted or null duration is continuous.

## Rehearse real H3 without publishing

This is optional and makes **paid Reactor and xAI (Grok 4.7) API calls**. The synthetic test signal does not.

```sh
python -m pip install -r requirements.txt
python run.py
```

Under **API keys**, paste your xAI and Reactor API keys and click **Save keys**. The dashboard displays missing keys even in Test signal mode. Saving does not verify keys, call either provider, or incur API charges. The fields clear after submitting; blank fields preserve existing keys. Use **Remove** to clear a key (including overriding a key from the environment). Stop the preview before changing keys.

Choose **H3 Turbo Realtime**, upload a reference image, enter an optional scene, and click **Start preview**. The usage note next to the button makes clear that this action uses Reactor and xAI credits until stopped; there is no separate checkbox. The UI sends the explicit paid-use acknowledgment required by the backend only for H3. **Grok 4.7** understands the reference image and writes structured scene directions; Reactor H3 generates video/audio for the local MP4 encoder. Your reference image and scene are sent to xAI and Reactor only when you choose the paid path.

The writer uses `grok-4.7` directly at `https://api.x.ai/v1`, with low reasoning effort for scene latency. The `openai` Python package remains only as an xAI-compatible HTTP client: no OpenAI API key, endpoint, or fallback is used. See the [Grok 4.7 documentation](https://docs.x.ai/developers/models/grok-4.7). The runtime safety classifier also uses Grok, fails closed on malformed responses/errors, and is not a dedicated moderation service or a guarantee of output safety. Live chat is not wired to it yet.

The model selector supports **H3 Turbo Realtime** (`reactor/h3-reference-to-video-turbo-realtime`) and **FastH3** (`reactor/fast-h3`). Both use Grok 4.7 for scene writing and the same local preview sink. Generation controls are hidden for Test signal.

### FastH3

Choose **FastH3**, enter a scene, then click **Start preview**. No reference image is required. Optionally choose a starting image and enable **Use starting frame**; the uploaded still anchors the first successfully queued clip. Subsequent clips are text-driven, not frame-chained. Switching models preserves your uploaded reference but uses a separate FastH3 preset for each run.

The adapter sends the documented `prompt`, `seconds`, and `metadata` fields, with `starting_frame` only when requested—never Turbo’s `reference_images`. It reads clip-duration and queue limits from live `get_state`, sets a 16:9 canvas, enables autoplay, and holds the last frame between clips. It re-uploads the opening image on reconnect rather than reusing stale session handles. Grok writes self-contained subject/camera/sound prompts with a conservative 900-byte application limit; FastH3’s own tokenizer still enforces its 1,024-token limit. See the official [FastH3 schema](https://docs.reactor.inc/model-api-reference/fast-h3/schema) and [overview](https://docs.reactor.inc/model-api-reference/fast-h3/overview).

FastH3 and Turbo are wired and tested offline; neither has been validated with a paid session in this quickstart yet. Automatic frame-to-frame continuation and ending-frame controls are not implemented.

The dashboard shows whether keys are present, never their values. Saved keys are stored **unencrypted** in `.local/credentials.json` with owner-only (`0600`) file permissions. This is local file protection, not an OS keychain. Anyone with access to your OS account can read them. No browser key storage. Alternatively, copy `.env.example` to `.env` and set `REACTOR_API_KEY` and `XAI_API_KEY` before starting the server. Saved dashboard settings override this repo’s `.env` and process environment, including explicit removals. Chat-driven control is outside this simple demo’s scope.

## Connect your X source

1. Open [X Live Studio → Manage Sources](https://x.com/i/live-studio/sources). Choose your source and open the key icon / Encoder setup.
2. Under **X destination** in this dashboard, paste the **RTMPS server URL** and **stream key**. Save destination does not contact X. Only X `*.pscp.tv` RTMPS endpoints on port 443 with the `/x` path are accepted; unencrypted RTMP is not supported.
3. Start a local preview. **Test signal** needs no paid API calls; the AI models do.
4. Click **Send to X** and confirm. This immediately transmits the current preview to the saved source. A public or scheduled broadcast already using that source can show it to viewers.
5. In X, use that **same source** for your broadcast and verify the incoming video/audio. Follow [X’s broadcast setup guide](https://help.x.com/en/using-x/how-to-use-live-producer); choose a private broadcast for testing where available. This dashboard does not create, publish, or end X broadcast records.

**Stop sending** disconnects the relay but keeps generation/preview running. **Stop** stops both. Closing the browser does neither. Restarting the Python server never resumes transmission automatically. If the local server becomes unreachable, inspect/stop its process before assuming the stream has ended.

The stream key is saved separately in `.local/x-destination.json`, unencrypted with owner-only (`0600`) permissions; it is never returned by the status API or sent to Grok/Reactor. Blank key input preserves the saved key; Remove clears the saved destination. While sending, FFmpeg receives the destination as a command-line argument, so local process-inspection tools may see it. Do not expose this dashboard over a tunnel or share process listings.

The relay starts from the latest completed preview clip (not old session history), remuxes H.264/AAC into one FLV stream, and verifies TLS. It adds preview buffering latency. It stops on failure, a stalled preview, or excessive lag; reconnect manually. “Sending video” reports encoder output, not proof of X reception or a public broadcast. X reception has been reported in a manual test; a repeatable paid-generation-to-X acceptance test is still pending.

Continuous generation has no automatic spending cap. Closing a tab does not stop it. Stop sessions deliberately and monitor provider usage; providers may bill already-started requests after cancellation.

## What is real, and what is still pending?

| Feature | Status |
| --- | --- |
| Dashboard, start/stop, continuous preview | Implemented; diagnostics retained in the backend |
| Synthetic video → FFmpeg → local browser playback | Implemented; offline acceptance tests |
| Model raw-frame/audio → local MP4 encoder | Implemented; synthetic frame acceptance test |
| Paid H3 Turbo Realtime / FastH3 session | Both adapters wired; no paid-run validation yet |
| Grok 4.7 image understanding and scene writing | Wired to xAI; offline request-contract tests, no paid run yet |
| API key inputs and missing-key warnings | Implemented; local-only persistence, no provider verification |
| Chat fixtures and counters | Removed from the dashboard; test API retained, no effect on generation |
| X chat OAuth / Activity API | Not integrated |
| X publishing | Explicit RTMPS relay; manual reception reported, repeatable end-to-end acceptance pending |
| Render deployment | Not configured |

## Privacy and local files

The server binds only to `127.0.0.1`. Host/origin checks reject cross-site control requests. Do not put it behind a tunnel or expose the dashboard publicly; this local version has no account authentication.

Uploaded references and generated show configuration live in `.local/shows/`; completed clips live in `.local/runs/<session>/`. These files, `.local/credentials.json`, `.local/x-destination.json`, and `.env` are ignored by Git. Do not share your `.local` directory or `.env` in screenshots, archives, or support reports. Each run uses a fresh directory; old clips are not overwritten. Only current-session completed clips are served. Rehearsal output and saved keys survive stopping, but session state and selected show reset when Python restarts.

Each run retains only its recent preview buffer, not the full recording. Recent buffers from stopped runs still accumulate between sessions; remove unwanted run folders after reviewing them. Older clips removed from an active preview buffer cannot be replayed. No recordings are uploaded by the preview player.

## Tests

```sh
python -m unittest discover -s tests -v
node --check web/app.js
```

With only local dependencies, the raw-frame test is skipped; install the full requirements to exercise it. Tests launch FFmpeg against generated input and loopback HTTP only, never a model service or X. They cover real encoded MP4 serving, start/stop/restart/auto-stop, paid consent, secret redaction, input validation, rejected live destinations, and cross-origin controls.

## Next milestone

Keep this demo focused on Grok scene writing, Reactor video generation, and delivery to X. Finish end-to-end validation and the quickstart article; chat-driven control is outside this demo’s scope. Keep public setup docs/static landing page separate from the private control room.

## Before publishing changes

Run `python scripts/check_release.py` to check the source allowlist and look for common credential patterns and saved local credential values. After staging, run `python scripts/check_release.py --tracked` to check the actual Git index. Never force-add `.local`, `.env`, recordings, or review screenshots. The checker prints only filenames and reasons, not credentials; it is a targeted guard, not a comprehensive secret scanner.

The runtime is adapted from the existing Twitch starter. See LICENSE and NOTICE for attribution.

The dashboard uses Reactor’s logo and brand palette from `@reactor-team/ui` 1.4.1 with system fonts. No font files are bundled, and there are no third-party font requests or frontend build dependencies.

The header uses the unmodified white X logo artwork from the [official X brand toolkit](https://about.x.com/en/who-we-are/brand-toolkit), displayed separately from Reactor’s logo. X trademarks remain subject to X’s brand guidelines; their inclusion indicates the intended integration, not sponsorship or endorsement.
