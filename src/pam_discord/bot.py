from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import discord
from dotenv import load_dotenv
from faster_whisper import WhisperModel

from .config import Config, load_config

LOG = logging.getLogger("pam_discord")
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"}


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned[:100] or "audio"


def _is_audio(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    return content_type.startswith("audio/") or Path(attachment.filename).suffix.lower() in AUDIO_EXTENSIONS


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class PamDiscord(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self._model: WhisperModel | None = None
        self._model_lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        self.config.archive_dir.mkdir(parents=True, exist_ok=True)

    async def on_ready(self) -> None:
        LOG.info("connected as %s; listening in %d mapped channel(s)", self.user, len(self.config.channels))

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author.id not in self.config.allowed_user_ids:
            return
        channel_config = self.config.channels.get(message.channel.id)
        if channel_config is None:
            return

        attachments = [item for item in message.attachments if _is_audio(item)]
        if not attachments:
            return
        for attachment in attachments:
            try:
                await self._handle_attachment(message, attachment, channel_config.workspace, channel_config.run_codex)
            except Exception:
                LOG.exception("failed to process message %s", message.id)
                await message.reply("I couldn't process that recording. Check the bot log for details.", mention_author=False)

    async def _handle_attachment(
        self,
        message: discord.Message,
        attachment: discord.Attachment,
        workspace: Path,
        run_codex: bool,
    ) -> None:
        if attachment.size > self.config.max_attachment_bytes:
            await message.reply("That recording exceeds the configured size limit.", mention_author=False)
            return
        duration = getattr(attachment, "duration_secs", None)
        if duration is not None and duration > self.config.max_audio_seconds:
            await message.reply("That recording exceeds the configured duration limit.", mention_author=False)
            return

        created = message.created_at.astimezone(UTC)
        record_dir = self.config.archive_dir / created.strftime("%Y/%m/%d") / str(message.id)
        record_dir.mkdir(parents=True, exist_ok=False)
        audio_path = record_dir / _safe_name(attachment.filename)
        await attachment.save(audio_path)

        metadata = {
            "message_id": message.id,
            "guild_id": message.guild.id if message.guild else None,
            "channel_id": message.channel.id,
            "author_id": message.author.id,
            "author": str(message.author),
            "created_at": created.isoformat(),
            "attachment_filename": attachment.filename,
            "attachment_bytes": attachment.size,
            "workspace": str(workspace),
            "run_codex": run_codex,
        }
        _write_json(record_dir / "metadata.json", metadata)

        async with message.channel.typing():
            # faster-whisper model instances are not assumed to be safe for concurrent calls.
            async with self._model_lock:
                transcript = await asyncio.to_thread(self._transcribe, audio_path)
            (record_dir / "transcript.txt").write_text(transcript + "\n", encoding="utf-8")
            await self._reply_chunks(message, f"**Transcript**\n{transcript}")

            if run_codex and transcript.strip():
                (record_dir / "prompt.txt").write_text(transcript + "\n", encoding="utf-8")
                output = await asyncio.to_thread(self._run_codex, transcript, workspace)
                (record_dir / "codex-output.txt").write_text(output + "\n", encoding="utf-8")
                await self._reply_chunks(message, f"**Codex**\n{output}")

    def _transcribe(self, audio_path: Path) -> str:
        if self._model is None:
            self._model = WhisperModel(
                self.config.whisper_model,
                device=self.config.whisper_device,
                compute_type=self.config.whisper_compute_type,
            )
        segments, info = self._model.transcribe(
            str(audio_path),
            beam_size=5,
            vad_filter=True,
        )
        if info.duration > self.config.max_audio_seconds:
            raise ValueError("decoded audio exceeds configured duration limit")
        return " ".join(segment.text.strip() for segment in segments).strip() or "[No speech detected]"

    def _run_codex(self, prompt: str, workspace: Path) -> str:
        # Argument-list execution avoids shell interpretation. Authentication is exclusively
        # the existing local `codex login`; this project never accepts an OpenAI API key.
        result = subprocess.run(
            [self.config.codex_binary, "exec", "-C", str(workspace), "--", prompt],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=self.config.codex_timeout_seconds,
            check=False,
            env={key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"},
        )
        combined = result.stdout.strip()
        if result.returncode != 0:
            detail = result.stderr.strip()[-2000:]
            raise RuntimeError(f"codex exited {result.returncode}: {detail}")
        return combined or "[Codex completed without text output]"

    @staticmethod
    async def _reply_chunks(message: discord.Message, text: str) -> None:
        for offset in range(0, len(text), 1900):
            await message.reply(text[offset : offset + 1900], mention_author=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Pam Discord voice inbox")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    args = parser.parse_args()
    load_dotenv()
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is missing; create a local .env from .env.example")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    PamDiscord(load_config(args.config)).run(token, log_handler=None)


if __name__ == "__main__":
    main()
