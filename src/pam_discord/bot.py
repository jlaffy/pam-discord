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

from .config import ChannelConfig, Config, load_config

LOG = logging.getLogger("pam_discord")
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"}


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned[:100] or "audio"


def _is_audio(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    return (
        content_type.startswith("audio/")
        or Path(attachment.filename).suffix.lower() in AUDIO_EXTENSIONS
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


class PamDiscord(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self._model: WhisperModel | None = None
        self._model_lock = asyncio.Lock()
        self._conversation_locks: dict[int, asyncio.Lock] = {}

    async def setup_hook(self) -> None:
        self.config.archive_dir.mkdir(parents=True, exist_ok=True)

    async def on_ready(self) -> None:
        LOG.info(
            "connected as %s; listening in %d mapped channel(s)",
            self.user,
            len(self.config.channels),
        )

    def _channel_config(self, channel: discord.abc.Messageable) -> ChannelConfig | None:
        channel_id = getattr(channel, "id", None)
        if isinstance(channel, discord.Thread):
            channel_id = channel.parent_id
        return self.config.channels.get(channel_id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author.id not in self.config.allowed_user_ids:
            return
        channel_config = self._channel_config(message.channel)
        if channel_config is None:
            return

        audio = [item for item in message.attachments if _is_audio(item)]
        if not message.content.strip() and not audio:
            return

        try:
            thread = await self._conversation_thread(message)
            lock = self._conversation_locks.setdefault(thread.id, asyncio.Lock())
            async with lock:
                await self._handle_message(message, thread, audio, channel_config)
        except Exception:
            LOG.exception("failed to process message %s", message.id)
            await message.reply(
                "I couldn't process that request. Check the bot log for details.",
                mention_author=False,
            )

    async def _conversation_thread(self, message: discord.Message) -> discord.Thread:
        if isinstance(message.channel, discord.Thread):
            return message.channel
        title = message.content.strip() or next(
            (_safe_name(item.filename) for item in message.attachments if _is_audio(item)),
            "voice-task",
        )
        title = re.sub(r"\s+", " ", title).strip()[:80]
        return await message.create_thread(name=title or "agent-task", auto_archive_duration=1440)

    async def _handle_message(
        self,
        message: discord.Message,
        thread: discord.Thread,
        attachments: list[discord.Attachment],
        channel_config: ChannelConfig,
    ) -> None:
        created = message.created_at.astimezone(UTC)
        conversation_dir = self.config.archive_dir / "conversations" / str(thread.id)
        record_dir = conversation_dir / "messages" / str(message.id)
        record_dir.mkdir(parents=True, exist_ok=False)

        transcripts: list[str] = []
        saved_audio: list[str] = []
        async with thread.typing():
            for attachment in attachments:
                if attachment.size > self.config.max_attachment_bytes:
                    raise ValueError(
                        f"attachment exceeds configured size limit: {attachment.filename}"
                    )
                duration = getattr(attachment, "duration_secs", None)
                if duration is not None and duration > self.config.max_audio_seconds:
                    raise ValueError(
                        f"attachment exceeds configured duration limit: {attachment.filename}"
                    )
                audio_path = record_dir / _safe_name(attachment.filename)
                await attachment.save(audio_path)
                saved_audio.append(audio_path.name)
                async with self._model_lock:
                    transcripts.append(await asyncio.to_thread(self._transcribe, audio_path))

            transcript = "\n\n".join(transcripts).strip()
            prompt_parts = [part for part in (message.content.strip(), transcript) if part]
            prompt = "\n\n".join(prompt_parts)
            if not prompt:
                prompt = "[No speech detected]"

            metadata = {
                "message_id": message.id,
                "thread_id": thread.id,
                "parent_channel_id": thread.parent_id,
                "guild_id": message.guild.id if message.guild else None,
                "author_id": message.author.id,
                "author": str(message.author),
                "created_at": created.isoformat(),
                "audio_files": saved_audio,
                "workspace": str(channel_config.workspace),
                "run_codex": channel_config.run_codex,
                "discord_jump_url": message.jump_url,
            }
            _write_json(record_dir / "metadata.json", metadata)
            if transcript:
                (record_dir / "transcript.txt").write_text(transcript + "\n", encoding="utf-8")
                await self._send_chunks(thread, f"**Transcript**\n{transcript}")
            (record_dir / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
            _append_jsonl(
                conversation_dir / "conversation.jsonl",
                {"role": "human", "prompt": prompt, **metadata},
            )

            if channel_config.run_codex:
                output, session_id = await asyncio.to_thread(
                    self._run_codex,
                    prompt,
                    channel_config.workspace,
                    conversation_dir,
                    record_dir,
                )
                (record_dir / "codex-output.txt").write_text(output + "\n", encoding="utf-8")
                _append_jsonl(
                    conversation_dir / "conversation.jsonl",
                    {
                        "role": "agent",
                        "agent": "codex",
                        "session_id": session_id,
                        "created_at": datetime.now(UTC).isoformat(),
                        "in_reply_to": message.id,
                        "output": output,
                    },
                )
                await self._send_chunks(thread, f"**Codex**\n{output}")

    def _transcribe(self, audio_path: Path) -> str:
        if self._model is None:
            self._model = WhisperModel(
                self.config.whisper_model,
                device=self.config.whisper_device,
                compute_type=self.config.whisper_compute_type,
            )
        segments, info = self._model.transcribe(str(audio_path), beam_size=5, vad_filter=True)
        if info.duration > self.config.max_audio_seconds:
            raise ValueError("decoded audio exceeds configured duration limit")
        transcript = " ".join(segment.text.strip() for segment in segments).strip()
        return transcript or "[No speech detected]"

    def _run_codex(
        self,
        prompt: str,
        workspace: Path,
        conversation_dir: Path,
        record_dir: Path,
    ) -> tuple[str, str]:
        state_path = conversation_dir / "state.json"
        session_id: str | None = None
        if state_path.exists():
            session_id = json.loads(state_path.read_text(encoding="utf-8")).get("codex_session_id")

        final_path = record_dir / "codex-final.txt"
        if session_id:
            command = [
                self.config.codex_binary,
                "exec",
                "resume",
                "--json",
                "-o",
                str(final_path),
                session_id,
                prompt,
            ]
        else:
            command = [
                self.config.codex_binary,
                "exec",
                "--json",
                "-C",
                str(workspace),
                "-o",
                str(final_path),
                "--",
                prompt,
            ]

        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=self.config.codex_timeout_seconds,
            check=False,
            env={key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"},
        )
        (record_dir / "codex-events.jsonl").write_text(result.stdout, encoding="utf-8")
        if result.returncode != 0:
            detail = result.stderr.strip()[-2000:]
            raise RuntimeError(f"codex exited {result.returncode}: {detail}")

        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started" and event.get("thread_id"):
                session_id = str(event["thread_id"])
        if not session_id:
            raise RuntimeError("Codex completed without returning a resumable session ID")

        _write_json(state_path, {"codex_session_id": session_id, "workspace": str(workspace)})
        output = final_path.read_text(encoding="utf-8").strip() if final_path.exists() else ""
        return output or "[Codex completed without text output]", session_id

    @staticmethod
    async def _send_chunks(channel: discord.abc.Messageable, text: str) -> None:
        for offset in range(0, len(text), 1900):
            await channel.send(text[offset : offset + 1900])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Pam Discord agent bridge")
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
