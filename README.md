# Pam Discord

**Turn a Discord voice note into durable, local, agent-ready work.**

Pam Discord is a small, self-hosted inbox for people who think out loud. Send an audio file or Discord voice message in a mapped channel; Pam saves it, transcribes it locally with `faster-whisper`, and can pass the transcript to `codex exec` in the right project directory.

```text
Discord voice/audio → local archive → faster-whisper → Codex → saved result + Discord reply
```

- Local transcription: no transcription API bill
- Existing Codex login: uses your local ChatGPT-authenticated Codex session
- Clear routing: one Discord channel per personal area or project
- Durable records: audio, transcript, metadata, and Codex output stay on your machine
- Explicit cost boundary: no OpenAI or Anthropic API key, and no automatic fallback to a metered API

> Pam Discord is an early, intentionally small project. Review its behavior and test it in a non-critical workspace before relying on it.

## What you need

- Python 3.11+
- A computer or server that can stay online while Pam listens
- A Discord server where you can add a bot
- The [Codex CLI](https://developers.openai.com/codex/cli/) installed and already signed in with `codex login`
- Enough disk space for your recordings and Whisper model; the model downloads on first use

You do **not** need an OpenAI API key or Anthropic API key. Pam removes `OPENAI_API_KEY` from the environment before launching Codex. If the local Codex login is missing or fails, the job fails visibly; it does not switch to metered API billing.

## Set up in about ten minutes

### 1. Create the Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications), select **New Application**, then open **Bot**.
2. Create/reset the bot token and keep it private.
3. Enable **Message Content Intent** under **Privileged Gateway Intents**.
4. Under **OAuth2 → URL Generator**, select the `bot` scope. Grant **View Channels**, **Send Messages**, **Read Message History**, and **Attach Files**, then use the generated URL to invite the bot to your server.

Never paste the bot token into chat, commit it, or put it in `config.toml`.

### 2. Install Pam

```bash
git clone https://github.com/YOUR-NAME/pam-discord.git
cd pam-discord
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

Confirm Codex is installed and authenticated:

```bash
codex login
```

Choose **Sign in with ChatGPT** when prompted. Pam uses that existing local session; it does not collect your ChatGPT credentials.

### 3. Configure secrets and routing

```bash
cp .env.example .env
cp config.example.toml config.toml
```

Put only the Discord token in `.env`:

```dotenv
DISCORD_BOT_TOKEN=your-private-bot-token
```

In Discord, enable **User Settings → Advanced → Developer Mode**. Right-click your profile to **Copy User ID**, and right-click each channel to **Copy Channel ID**. Then edit `config.toml`:

```toml
archive_dir = "./archive"
allowed_user_ids = [111111111111111111]
whisper_model = "small.en"

[channels."222222222222222222"]
workspace = "/absolute/path/to/agent-native-genomics"
run_codex = true

[channels."333333333333333333"]
workspace = "/absolute/path/to/personal"
run_codex = false

[channels."444444444444444444"]
workspace = "/absolute/path/to/another-project"
run_codex = true
```

Each channel is an independent inbox. `workspace` tells Codex which directory to work in; `run_codex = false` makes a channel transcription-only. Only users listed in `allowed_user_ids` are processed. Use absolute workspace paths and give the bot access only to the Discord channels it needs.

### 4. Run it

```bash
source .venv/bin/activate
pam-discord --config config.toml
```

Send a Discord voice message or attach an audio file in a mapped channel. Pam replies with the transcript, then the Codex result when enabled. Stop it with `Ctrl+C`. For continuous use, run the same command under your preferred service manager (for example, systemd or a container supervisor).

## What gets saved

The archive is local and chronological:

```text
archive/
└── 2026/07/20/<discord-message-id>/
    ├── recording.ogg
    ├── metadata.json
    ├── transcript.txt
    └── codex-output.txt    # only when Codex runs
```

`metadata.json` records the message, author, channel, time, workspace, and routing choice. Discord retains its own message thread according to your server settings; the local archive remains the source record under your control.

## Voice today

This release processes **voice messages and uploaded audio attachments**. It does not join, listen to, or record Discord live voice channels. Discord's recording controls can also differ by client; if your desktop app does not offer a voice-message record button, record with your operating system and upload the audio file, or send the voice message from mobile.

A lightweight desktop recorder and upload shortcut are natural roadmap items. They are not part of the current release.

## Privacy and consent

Audio and transcripts can contain highly sensitive information. Tell everyone being recorded, obtain any consent required where you live, and follow your organization’s policies. Keep `.env`, `config.toml`, and `archive/` out of version control; restrict filesystem access; use private Discord channels; and define a retention/deletion policy. Codex receives the transcript only in channels where `run_codex = true`.

## Terms used here

- **Discord bot:** the application account that receives messages and posts replies.
- **Mapped channel:** a Discord channel ID connected to one local workspace.
- **Workspace:** the local project directory passed to Codex.
- **Local transcription:** speech-to-text performed on your machine by `faster-whisper`.
- **Archive:** the local, timestamped record of audio, metadata, transcript, and result.
- **Codex result:** stdout returned by `codex exec`; any workspace changes Codex makes remain in that workspace.

## Contributing

Issues and focused pull requests are welcome. Please do not include real recordings, transcripts, tokens, private paths, or other personal data in bug reports.

Licensed under the MIT License.
