![Infinite X Livestream — Reactor livestream quickstart](https://inc-reactor-static.b-cdn.net/assets/repo-banners/x-livestream-quickstart/infinite-x-livestream-fcf792e6cfc1.png)

# FastH3-Omni Livestreams on X

Run a continuous FastH3-Omni show on X. Describe a scene, preview generated video and audio locally, relay audience ideas into the show, and explicitly send the feed to your X Live Studio source.

[Reactor](https://reactor.inc) powers generation. Grok 4.7 writes scene directions and reviews incoming suggestions. The built-in encoder handles delivery to X. Run it on your computer: no GPU, OBS setup, or cloud deployment required.

## Go live

You'll need:

- **macOS or Linux**, Python 3.12+, and FFmpeg. Windows is not currently supported.
- A **Reactor API key** with access to your chosen H3 model.
- An **[xAI API key](https://console.x.ai/team/default/api-keys)** with access to Grok 4.7.
- An **X account with Live Studio access**, plus your source's RTMPS URL and stream key.

### 1. Launch the dashboard

Install FFmpeg if needed: `brew install ffmpeg` on macOS or `sudo apt install ffmpeg` on Ubuntu.

```sh
git clone https://github.com/willemhelmet/x-livestream-quickstart.git
cd x-livestream-quickstart

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

The dashboard opens at **[localhost:8091](http://127.0.0.1:8091/)**. Keep the Python process running while you stream.

### 2. Connect your accounts

Under **API keys**, enter your Reactor and xAI keys, then click **Save keys**.

Open **[X Live Studio → Manage Sources](https://x.com/i/live-studio/sources)**. Create or select a source, then open its key icon / **Encoder setup**. Copy these two values into **X destination** in the dashboard:

- **Server URL (RTMPS)** — the server address, without the stream key.
- **Stream key** — the key for that source.

Click **Save destination**. Saving credentials does not start generation or send video.

### 3. Choose your show

**FastH3-Omni** is the default. Describe what your show is about, choose its visual direction and a supported scene length. Grok writes each next beat as the show runs. Expand **Camera & sound** for framing, camera movement, ambience, music and exact dialogue/voice direction.

**Speech** offers short scripted lines, **Say this exact line**, or **No speech**. Set a stable speaker, voice and language for a presenter-led show. Grok returns one typed vocal event per clip, separate from the visible action; the app validates it and places only the language and exact words inside `<d>[English] …</d>` within the shot. Lines are capped at roughly two words per second after a 1.5-second allowance, with an absolute maximum of twelve words. Keep them complete, with sentence punctuation. No-speech clips explicitly forbid dialogue/background voices and keep characters' lips closed. Invalid output gets one repair attempt, not an unchecked path to the video model. This follows [MiniMax's upstream H3 prompting guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md); the deployed model still needs a listening test, and the format alone cannot guarantee intelligible speech.

**Image & audio references** accepts up to nine ordered images and three audio clips (two when carrying scenes). Give each a role and description: character, object or location/look; voice, ambience or complete soundtrack. Audio must be 2–15 seconds, mono or stereo, and needs an image for the opening scene. A complete soundtrack suppresses generated sound/music/dialogue directions. Uploads are validated and saved locally; Reactor receives them only when generation starts. Grok receives their text descriptions, not the uploaded media.

**Carry motion and sound into the next scene** uses the most recent generated clip as a latent continuation source. Turn it off for independent shots. Appearance and audio references remain attached to every scene. **Opening keyframes** can pin the first and/or last frame of the opening scene, alongside at least one appearance reference. These keyframes are not applied to later scenes; latent continuation cannot accept them. Frame-mode continuation and per-scene keyframe editing are not exposed in this continuous-show UI.

**Inspect prompt structure** shows how your premise and directions are assembled with numbered `<Subject N>`, `<Picture N>` and `<Audio N>` definitions. Grok supplies the action, camera, sound, music and optional speech as validated JSON; code compiles the canonical shot/soundscape/music sections, and the full reference sections when uploads are present. The inspection is illustrative for scripted lines; exact-line mode shows your words. **Prompt for the current scene**, below the preview, shows the actual queued prompt once that scene plays. Large combinations of references and text can still exceed the deployment's request budget; the model remains the authority on admission.

**Save show** stores your narrative settings on this computer without starting generation. The app also saves them when you start preview, and restores them when you reopen the dashboard.

For example:

> A tiny late-night ramen shop in rainy Tokyo. The chef prepares a bowl while the camera slowly moves along the counter. Warm lighting, rain outside, quiet kitchen sounds.

Click **Start preview** to start generating video and audio. Grok keeps writing new scene directions as the stream runs; you don't need to queue every clip yourself.

**Playback buffer** defaults to **16 seconds of finished scenes** before autoplay starts. Generation and writing run ahead within the deployment's queue limits; unfinished requests do not count toward the reserve. When it runs out, the model holds its last frame while the reserve rebuilds instead of immediately playing each isolated arrival. With clip continuation, builds stay sequential so each new clip continues the immediately preceding generated clip, and ready scenes stay in that order. Viewer suggestions therefore arrive after the already-built reserve rather than jumping ahead of their continuation source. Grok sees accepted future beats as well as played history, so it doesn't repeat the opening while warming the buffer.

Increase the buffer to absorb generation-time spikes; it adds startup time, upfront generation credits and audience-response latency. The effective target is capped by ready-queue capacity. The dashboard reports finished seconds ahead and, when available, model build speed relative to playback. **A buffer cannot fix a producer that stays slower than realtime**: it will eventually run out. This intentionally borrows just the bounded-runway and structured-expansion ideas from the [Dazzle specification](https://connerruhl-exports.pages.dev/dazzle-specification-324a0f/#1-overview), not its whole agent/planner/transport stack.

Below **Live Preview**, the dashboard shows the current scene, upcoming scenes, queue counts, and whether model video and audio have arrived. Unmute the preview player to listen before sending the feed to X.

**Generation uses paid Reactor and xAI credits until you stop.** There is no automatic spending cap.

### 4. Send it to X

Once video appears and the playback reserve is ready, click **Send to X** and confirm. The dashboard prevents starting transmission during initial buffering or a refill.

In X Live Studio, create or open a broadcast using **the same source**. Verify the incoming picture and sound, then start your broadcast in X. See [X's broadcast setup guide](https://help.x.com/en/using-x/how-to-use-live-producer) for the source-to-broadcast flow.

**Already have an active broadcast on that source? Your video may reach viewers as soon as you confirm Send to X.** This dashboard sends the feed; broadcast creation, visibility, scheduling, and ending the broadcast are managed in X.

You're streaming. Leave the dashboard and Python worker running, and keep an eye on the incoming feed in X Live Studio.

### Let viewers shape future scenes

The dashboard's **Audience scene suggestions** box relays ideas to the running model worker. Paste in a viewer's idea from X, or type one yourself; Grok reviews it before it can shape a future clip. “Queued” means sent for review—not approved or already on air. Watch the worker log and preview to confirm the result. **X Live Studio comments and X Post replies are not ingested automatically.** This quickstart uses a deliberate manual relay; it does not require an X Developer App or pretend to read livestream chat.

## Look before you leap

Want to check the setup before an audience sees it? Stay in **Live Preview** for as long as you like before choosing **Send to X**.

For a check without AI costs, select **Test signal** and click **Start preview**. This generates local video and silent audio, with no Reactor or xAI calls. You can also send the test signal to X to verify delivery—check that the source isn't attached to a public broadcast, or use a private test broadcast where available.

Local playback is buffered in two-second clips, so some delay and small transitions are expected. Always verify picture and sound in X as well; the dashboard's “Sending video” status reports encoder output, not confirmation that your audience can see it.

## Run the stream

- **Stop sending** disconnects from X while generation and local preview continue.
- **Stop** stops both generation and transmission. End the broadcast in X separately.
- **Closing the browser tab stops neither.** Use the controls or press Ctrl+C in the terminal.
- The AI worker monitors its dashboard process and shuts down if that process exits or crashes. A browser tab can close while the dashboard's Python process stays running.
- After a server restart, transmission stays off until you explicitly start it again.
- If the X connection fails or falls behind, check the destination and network, then click **Send to X** again.

Keep your computer awake and connected. Monitor provider usage; requests already in progress may still be billed after you stop.

## Your keys stay on your computer

API keys and the stream key are saved locally, not in browser storage or this repository. The dashboard displays whether they're present without returning their values.

Blank key fields preserve saved keys; **Remove** clears them. Stop generation before changing API keys, and stop sending before changing the X destination.

<details>
<summary>Storage and security details</summary>

Keys are stored **unencrypted** in owner-only files: `.local/credentials.json` and `.local/x-destination.json`. This is file-permission protection, not an OS keychain. Anyone with access to your OS account may read them. While sending, the stream key is also passed to FFmpeg and may be visible through local process-inspection tools.

Alternatively, set `REACTOR_API_KEY` and `XAI_API_KEY` in this repository's `.env`; saved dashboard settings override those values. Don't share `.env`, `.local`, or process listings.

The dashboard binds to `127.0.0.1` and has no account authentication. Keep it local—don't expose it through a tunnel or public server.

Scene descriptions and creative direction are sent to Reactor and xAI. Uploaded image/audio references and keyframes go to Reactor; xAI receives their descriptions only. Video and audio are sent to X only after **Send to X**. Only X `*.pscp.tv` RTMPS endpoints on port 443 with the `/x` path are supported; TLS verification is enabled.

</details>

## What's included

A local narrative dashboard, structured camera/sound/music/dialogue prompting, ordered image/audio references, opening keyframes, Grok 4.7 scene writing and suggestion moderation, FastH3-Omni clip continuation, scene pacing, continuous generation, operator-relayed scene suggestions, live preview, and explicit RTMPS delivery to X.

The scope is intentionally focused: **generate a show and broadcast it**. Automatic X chat ingestion, hosted multi-user administration, and full-session recording are not included. Preview retains a rolling buffer of roughly two minutes. Credentials, saved narrative settings and numbered reference uploads survive shutdown. After a server restart, explicitly start preview and transmission. The legacy single opening-reference control requires reuploading after a server restart; use the numbered reference library for new shows.

FastH3-Omni uses the published model ID `reactor/fast-h3-omni`. The worker uses the Python `reactor-sdk` transport and follows the inspected `@reactor-models/fast-h3-omni` v0.2.0 wire contract: five supported durations, `reference_image`, `continue_from_clip_id` with latent continuation, `clip_seconds_choices` state, and the documented autoplay/canvas/flush commands. It uses direct sessions, rather than the batch-generation API. See the [FastH3-Omni overview](https://docs.reactor.inc/model-api-reference/fast-h3-omni/overview) and [schema](https://docs.reactor.inc/model-api-reference/fast-h3-omni/schema).

## Development

`python run.py --no-browser` starts without opening a tab. Use `--port 8092` to change the dashboard port. For test-signal-only use, `requirements-local.txt` provides the smaller dependency set.

```sh
python -m unittest discover -s tests -v
node --check web/app.js
python scripts/check_release.py
```

Tests use synthetic media and local HTTP servers, not paid model calls or X. X reception has been reported in a manual test; repeatable paid-generation-to-X validation remains pending. Grok also provides the runtime safety classifier, but it is not a guarantee of output safety—review the feed you broadcast.

For a bounded private model check, run `python scripts/smoke_omni.py`. This spends Reactor credits on one 5.875-second clip, requests the served schema, checks for actual video and non-silent audio, and saves evidence under ignored `test-results/omni-smoke/`. It never sends to X and closes the session on completion or timeout. This model check does not replace verifying picture and sound in X Live Studio.

Before publishing changes, run `python scripts/check_release.py --tracked` after staging. The checker scans the source allowlist and actual Git index for common secret patterns and saved credential values; it isn't a comprehensive secret scanner. Never force-add private settings, recordings, or review screenshots.

## License and attribution

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for runtime and brand attribution.

Reactor and X logos identify the integration; they do not imply endorsement. X artwork comes from the [official brand toolkit](https://about.x.com/en/who-we-are/brand-toolkit) and remains subject to X's brand guidelines. System fonts are used, with no bundled font files or frontend build step.
