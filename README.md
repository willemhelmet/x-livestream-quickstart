# MiniMax H3 Livestreams on X

Your one-stop shop for running MiniMax H3 livestreams on X. Describe a scene, choose your model, and send generated video and audio straight to your X Live Studio source—all from one browser dashboard.

[Reactor](https://reactor.inc) powers H3 generation. Grok 4.7 writes the scene directions. The built-in encoder handles delivery to X. Run it on your computer: no GPU, OBS setup, or cloud deployment required.

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

Choose a model and describe what you want on screen:

- **FastH3:** start with a scene description. Optionally upload an image and enable **Use starting frame** to anchor the opening clip.
- **H3 Turbo Realtime:** upload a reference image, then add an optional scene description.

For example:

> A tiny late-night ramen shop in rainy Tokyo. The chef prepares a bowl while the camera slowly moves along the counter. Warm lighting, rain outside, quiet kitchen sounds.

Click **Start preview** to start generating video and audio. Grok keeps writing new scene directions as the stream runs; you don't need to queue every clip yourself.

**Generation uses paid Reactor and xAI credits until you stop.** There is no automatic spending cap.

### 4. Send it to X

Once video appears, click **Send to X** and confirm.

In X Live Studio, create or open a broadcast using **the same source**. Verify the incoming picture and sound, then start your broadcast in X. See [X's broadcast setup guide](https://help.x.com/en/using-x/how-to-use-live-producer) for the source-to-broadcast flow.

**Already have an active broadcast on that source? Your video may reach viewers as soon as you confirm Send to X.** This dashboard sends the feed; broadcast creation, visibility, scheduling, and ending the broadcast are managed in X.

You're streaming. Leave the dashboard and Python worker running, and keep an eye on the incoming feed in X Live Studio.

## Look before you leap

Want to check the setup before an audience sees it? Stay in **Live Preview** for as long as you like before choosing **Send to X**.

For a check without AI costs, select **Test signal** and click **Start preview**. This generates local video and silent audio, with no Reactor or xAI calls. You can also send the test signal to X to verify delivery—check that the source isn't attached to a public broadcast, or use a private test broadcast where available.

Local playback is buffered in two-second clips, so some delay and small transitions are expected. Always verify picture and sound in X as well; the dashboard's “Sending video” status reports encoder output, not confirmation that your audience can see it.

## Run the stream

- **Stop sending** disconnects from X while generation and local preview continue.
- **Stop** stops both generation and transmission. End the broadcast in X separately.
- **Closing the browser tab stops neither.** Use the controls or press Ctrl+C in the terminal.
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

Scene descriptions and reference images are sent to Reactor and xAI for generation. Video and audio are sent to X only after **Send to X**. Only X `*.pscp.tv` RTMPS endpoints on port 443 with the `/x` path are supported; TLS verification is enabled.

</details>

## What's included

A local dashboard, Grok 4.7 scene writing, two Reactor H3 adapters, reference-image upload, continuous generation, live preview, and explicit RTMPS delivery to X.

The scope is intentionally focused: **generate a show and broadcast it**. Audience-chat control, hosted multi-user administration, and full-session recording are not included. Preview retains a rolling buffer of roughly two minutes; saved credentials and recent clips survive shutdown, but the selected show must be set up again after a server restart.

FastH3's optional starting image anchors the first queued clip; subsequent clips are text-driven, not automatically frame-chained. Model references: [FastH3 overview](https://docs.reactor.inc/model-api-reference/fast-h3/overview) and [schema](https://docs.reactor.inc/model-api-reference/fast-h3/schema).

## Development

`python run.py --no-browser` starts without opening a tab. Use `--port 8092` to change the dashboard port. For test-signal-only use, `requirements-local.txt` provides the smaller dependency set.

```sh
python -m unittest discover -s tests -v
node --check web/app.js
python scripts/check_release.py
```

Tests use synthetic media and local HTTP servers, not paid model calls or X. X reception has been reported in a manual test; repeatable paid-generation-to-X validation remains pending. Grok also provides the runtime safety classifier, but it is not a guarantee of output safety—review the feed you broadcast.

Before publishing changes, run `python scripts/check_release.py --tracked` after staging. The checker scans the source allowlist and actual Git index for common secret patterns and saved credential values; it isn't a comprehensive secret scanner. Never force-add private settings, recordings, or review screenshots.

## License and attribution

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for runtime and brand attribution.

Reactor and X logos identify the integration; they do not imply endorsement. X artwork comes from the [official brand toolkit](https://about.x.com/en/who-we-are/brand-toolkit) and remains subject to X's brand guidelines. System fonts are used, with no bundled font files or frontend build step.
