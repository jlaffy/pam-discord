from __future__ import annotations

import argparse
import atexit
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import socket
from datetime import UTC, datetime
from pathlib import Path

import discord
from dotenv import load_dotenv
from faster_whisper import WhisperModel

from .app_server import CodexAppServer, load_shared_sessions, save_shared_sessions
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


def _append_markdown(path: Path, heading: str, body: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"## {heading}\n\n{body.strip()}\n\n")


def _polled_sessions_path(workspace: Path) -> Path:
    return workspace / ".pam" / "polled-sessions.json"


def _load_polled_sessions(workspace: Path) -> set[str]:
    path = _polled_sessions_path(workspace)
    if not path.exists():
        return set()
    return {str(value) for value in json.loads(path.read_text(encoding="utf-8"))}


def _enable_session_polling(workspace: Path, codex_thread_id: str) -> None:
    sessions = _load_polled_sessions(workspace)
    sessions.add(codex_thread_id)
    path = _polled_sessions_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, sorted(sessions))


def _acquire_instance_lock(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise SystemExit(
            f"Pam already has an instance lock at {path}. "
            "Stop the other instance, or remove a confirmed stale lock."
        ) from exc
    _write_json(
        path / "owner.json",
        {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat(),
        },
    )

    def release() -> None:
        try:
            (path / "owner.json").unlink(missing_ok=True)
            path.rmdir()
        except OSError:
            LOG.warning("could not release instance lock %s", path)

    atexit.register(release)


class PamDiscord(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self._model: WhisperModel | None = None
        self._model_lock = asyncio.Lock()
        self._conversation_locks: dict[int, asyncio.Lock] = {}
        self._app_server = CodexAppServer(
            self.config.codex_app_server_url, self._handle_app_server_notification
        )
        self._link_watcher: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        self.config.archive_dir.mkdir(parents=True, exist_ok=True)
        await self._app_server.start(self.config.codex_binary)
        self._link_watcher = asyncio.create_task(self._watch_link_requests())

    async def close(self) -> None:
        if self._link_watcher is not None:
            self._link_watcher.cancel()
        await self._app_server.close()
        await super().close()

    async def on_ready(self) -> None:
        LOG.info(
            "connected as %s; listening in %d project server(s) and %d channel override(s)",
            self.user,
            len(self.config.guilds),
            len(self.config.channels),
        )

    def _channel_config(self, channel: discord.abc.Messageable) -> ChannelConfig | None:
        channel_id = getattr(channel, "id", None)
        if isinstance(channel, discord.Thread):
            channel_id = channel.parent_id
        channel_config = self.config.channels.get(channel_id)
        if channel_config is not None:
            return channel_config
        guild = getattr(channel, "guild", None)
        guild_id = getattr(guild, "id", None)
        return self.config.guilds.get(guild_id)

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
        thread = await message.create_thread(
            name=title or "agent-task",
            auto_archive_duration=1440,
        )
        await self._add_collaborators(thread, message)
        return thread

    async def _add_collaborators(
        self,
        thread: discord.Thread,
        message: discord.Message,
    ) -> None:
        if message.guild is None:
            return
        for user_id in self.config.allowed_user_ids:
            if user_id == message.author.id:
                continue
            try:
                member = message.guild.get_member(user_id)
                if member is None:
                    member = await message.guild.fetch_member(user_id)
                await thread.add_user(member)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                LOG.warning("could not add authorized user %s to thread %s", user_id, thread.id)

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
            agent_prompt = "\n\n".join(
                part for part in (channel_config.instruction_prefix, prompt) if part
            )

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
            (record_dir / "agent-prompt.txt").write_text(agent_prompt + "\n", encoding="utf-8")
            _append_jsonl(
                conversation_dir / "conversation.jsonl",
                {"role": "human", "prompt": prompt, **metadata},
            )
            project_record = self._record_project_message(
                channel_config,
                thread,
                record_dir,
                metadata,
                prompt,
                transcript,
            )

            shared_thread_id = self._shared_codex_thread(channel_config.workspace, thread.id)
            if shared_thread_id is not None:
                await self._app_server.request(
                    "thread/resume", {"threadId": shared_thread_id}
                )
                await self._app_server.request(
                    "turn/start",
                    {
                        "threadId": shared_thread_id,
                        "input": [{"type": "text", "text": agent_prompt}],
                        "clientUserMessageId": f"discord:{message.id}",
                        "approvalPolicy": "never",
                    },
                )
                return

            if channel_config.run_codex:
                output, session_id = await asyncio.to_thread(
                    self._run_codex,
                    agent_prompt,
                    channel_config.workspace,
                    conversation_dir,
                    record_dir,
                )
                (record_dir / "codex-output.txt").write_text(output + "\n", encoding="utf-8")
                sessions = load_shared_sessions(channel_config.workspace)
                sessions[session_id] = thread.id
                save_shared_sessions(channel_config.workspace, sessions)
                if project_record is not None:
                    metadata_path = project_record[0] / "metadata.json"
                    project_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    project_metadata["codex_thread_id"] = session_id
                    _write_json(metadata_path, project_metadata)
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
                if project_record is not None:
                    project_conversation_dir, project_message_dir = project_record
                    shutil.copytree(record_dir, project_message_dir, dirs_exist_ok=True)
                    agent_record = {
                        "role": "agent",
                        "agent": "codex",
                        "session_id": session_id,
                        "created_at": datetime.now(UTC).isoformat(),
                        "in_reply_to": message.id,
                        "output": output,
                    }
                    _append_jsonl(project_conversation_dir / "conversation.jsonl", agent_record)
                    _append_markdown(
                        project_conversation_dir / "conversation.md",
                        f"Codex · {agent_record['created_at']}",
                        output,
                    )

    def _record_project_message(
        self,
        channel_config: ChannelConfig,
        thread: discord.Thread,
        record_dir: Path,
        metadata: dict[str, object],
        prompt: str,
        transcript: str,
    ) -> tuple[Path, Path] | None:
        if channel_config.project_record_dir is None:
            return None
        project_dir = channel_config.project_record_dir / str(thread.id)
        project_dir.mkdir(parents=True, exist_ok=True)
        project_message_dir = project_dir / "messages" / record_dir.name
        shutil.copytree(record_dir, project_message_dir, dirs_exist_ok=True)
        project_metadata = project_dir / "metadata.json"
        if not project_metadata.exists():
            _write_json(
                project_metadata,
                {
                    "discord_thread_id": thread.id,
                    "discord_parent_channel_id": thread.parent_id,
                    "workspace": str(channel_config.workspace),
                    "created_at": metadata["created_at"],
                },
            )
        human_record = {
            "role": "human",
            "prompt": prompt,
            "transcript": transcript or None,
            **metadata,
        }
        _append_jsonl(project_dir / "conversation.jsonl", human_record)
        _append_markdown(
            project_dir / "conversation.md",
            f"{metadata['author']} · {metadata['created_at']}",
            prompt,
        )
        return project_dir, project_message_dir

    def _shared_codex_thread(self, workspace: Path, discord_thread_id: int) -> str | None:
        for codex_thread_id, mapped_discord_id in load_shared_sessions(workspace).items():
            if mapped_discord_id == discord_thread_id:
                return codex_thread_id
        return None

    def _workspace_config_for_cwd(self, cwd: Path) -> ChannelConfig | None:
        matches = [
            item
            for item in self.config.guilds.values()
            if cwd == item.workspace or cwd.is_relative_to(item.workspace)
        ]
        return max(matches, key=lambda item: len(item.workspace.parts), default=None)

    async def _handle_app_server_notification(self, event: dict[str, object]) -> None:
        method = event.get("method")
        params = event.get("params")
        if not isinstance(params, dict):
            return
        if method == "thread/started":
            thread_value = params.get("thread")
            if isinstance(thread_value, dict):
                await self._link_started_codex_thread(thread_value)
                self._record_app_server_event(str(thread_value.get("id") or ""), event)
        elif method == "item/completed":
            self._record_app_server_event(str(params.get("threadId") or ""), event)
            await self._mirror_completed_codex_item(params)
        else:
            self._record_app_server_event(str(params.get("threadId") or ""), event)

    def _record_app_server_event(
        self, codex_thread_id: str, event: dict[str, object]
    ) -> None:
        if not codex_thread_id:
            return
        for channel_config in self.config.guilds.values():
            discord_thread_id = load_shared_sessions(channel_config.workspace).get(codex_thread_id)
            if discord_thread_id is None or channel_config.project_record_dir is None:
                continue
            conversation_dir = channel_config.project_record_dir / str(discord_thread_id)
            conversation_dir.mkdir(parents=True, exist_ok=True)
            _append_jsonl(conversation_dir / "codex-events.jsonl", event)
            return

    async def _watch_link_requests(self) -> None:
        request_dir = self.config.archive_dir.parent / "link-requests"
        request_dir.mkdir(parents=True, exist_ok=True)
        while True:
            for path in request_dir.glob("*.json"):
                try:
                    request = json.loads(path.read_text(encoding="utf-8"))
                    result = await self._app_server.request(
                        "thread/read",
                        {"threadId": str(request["thread_id"]), "includeTurns": True},
                    )
                    thread = result.get("thread") if isinstance(result, dict) else None
                    if isinstance(thread, dict):
                        await self._link_started_codex_thread(thread)
                        cwd = thread.get("cwd")
                        if isinstance(cwd, str):
                            channel_config = self._workspace_config_for_cwd(
                                Path(cwd).resolve()
                            )
                            if channel_config is not None:
                                thread_id = str(thread.get("id") or "")
                                _enable_session_polling(
                                    channel_config.workspace, thread_id
                                )
                                await self._import_codex_history(thread_id)
                    path.unlink()
                except Exception:
                    LOG.exception("failed to process Pam link request %s", path)
            await self._sync_shared_sessions()
            await asyncio.sleep(2)

    async def _sync_shared_sessions(self) -> None:
        """Import turns written by Codex clients that predate `pam codex`."""
        seen: set[str] = set()
        for channel_config in self.config.guilds.values():
            for codex_thread_id in _load_polled_sessions(channel_config.workspace):
                if codex_thread_id in seen:
                    continue
                seen.add(codex_thread_id)
                try:
                    await self._import_codex_history(codex_thread_id)
                except Exception:
                    LOG.exception("failed to sync Codex conversation %s", codex_thread_id)

    async def _link_started_codex_thread(self, value: dict[str, object]) -> None:
        codex_thread_id = str(value.get("id") or "")
        cwd_value = value.get("cwd")
        if not codex_thread_id or not isinstance(cwd_value, str):
            return
        channel_config = self._workspace_config_for_cwd(Path(cwd_value).resolve())
        if channel_config is None:
            return
        sessions = load_shared_sessions(channel_config.workspace)
        if codex_thread_id in sessions:
            return
        parent = next(
            (
                self.get_channel(channel_id)
                for channel_id, item in self.config.channels.items()
                if item.workspace == channel_config.workspace
            ),
            None,
        )
        if not isinstance(parent, discord.TextChannel):
            LOG.warning("no default Discord channel for Codex thread %s", codex_thread_id)
            return
        preview = str(value.get("name") or value.get("preview") or "").strip()
        title = re.sub(r"\s+", " ", preview)[:80] or f"Codex {codex_thread_id[:8]}"
        discord_thread = await parent.create_thread(
            name=title,
            auto_archive_duration=1440,
            type=discord.ChannelType.public_thread,
        )
        sessions[codex_thread_id] = discord_thread.id
        save_shared_sessions(channel_config.workspace, sessions)
        if channel_config.project_record_dir is not None:
            conversation_dir = channel_config.project_record_dir / str(discord_thread.id)
            conversation_dir.mkdir(parents=True, exist_ok=True)
            _write_json(
                conversation_dir / "metadata.json",
                {
                    "codex_thread_id": codex_thread_id,
                    "discord_thread_id": discord_thread.id,
                    "discord_parent_channel_id": parent.id,
                    "workspace": str(channel_config.workspace),
                    "created_at": datetime.now(UTC).isoformat(),
                    "source": "terminal",
                },
            )
        await discord_thread.send("**Pam** · Shared terminal and Discord Codex session connected.")
        await self._import_codex_history(codex_thread_id)

    async def _import_codex_history(self, codex_thread_id: str) -> None:
        result = await self._app_server.request(
            "thread/read", {"threadId": codex_thread_id, "includeTurns": True}
        )
        thread = result.get("thread") if isinstance(result, dict) else None
        turns = thread.get("turns", []) if isinstance(thread, dict) else []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            for item in turn.get("items", []):
                if isinstance(item, dict):
                    await self._mirror_completed_codex_item(
                        {"threadId": codex_thread_id, "item": item}
                    )

    async def _mirror_completed_codex_item(self, params: dict[str, object]) -> None:
        codex_thread_id = str(params.get("threadId") or "")
        item = params.get("item")
        if not codex_thread_id or not isinstance(item, dict):
            return
        for channel_config in self.config.guilds.values():
            discord_thread_id = load_shared_sessions(channel_config.workspace).get(codex_thread_id)
            if discord_thread_id is None:
                continue
            record_dir = channel_config.project_record_dir
            item_id = str(item.get("id") or "")
            imported_path = (
                record_dir / str(discord_thread_id) / "imported-items.json"
                if record_dir is not None
                else None
            )
            imported = set()
            if imported_path is not None and imported_path.exists():
                imported = set(json.loads(imported_path.read_text(encoding="utf-8")))
            if item_id and item_id in imported:
                return
            discord_thread = self.get_channel(discord_thread_id)
            if not isinstance(discord_thread, discord.Thread):
                try:
                    discord_thread = await self.fetch_channel(discord_thread_id)
                except discord.HTTPException:
                    return
            item_type = item.get("type")
            if item_type == "userMessage":
                if str(item.get("clientId") or "").startswith("discord:"):
                    if item_id and imported_path is not None:
                        imported.add(item_id)
                        imported_path.parent.mkdir(parents=True, exist_ok=True)
                        imported_path.write_text(
                            json.dumps(sorted(imported), indent=2) + "\n", encoding="utf-8"
                        )
                    return
                content = item.get("content")
                text = "\n".join(
                    str(part.get("text"))
                    for part in content if isinstance(part, dict) and part.get("type") == "text"
                ) if isinstance(content, list) else ""
                role, label = "human", "Terminal"
            elif item_type == "agentMessage":
                text = str(item.get("text") or "")
                role, label = "agent", "Codex"
            else:
                return
            if not text:
                return
            if record_dir is not None:
                conversation_dir = record_dir / str(discord_thread_id)
                conversation_dir.mkdir(parents=True, exist_ok=True)
                _append_jsonl(
                    conversation_dir / "conversation.jsonl",
                    {
                        "role": role,
                        "source": "terminal" if role == "human" else "codex",
                        "codex_thread_id": codex_thread_id,
                        "created_at": datetime.now(UTC).isoformat(),
                        "text": text,
                    },
                )
                _append_markdown(
                    conversation_dir / "conversation.md",
                    f"{label} · {datetime.now(UTC).isoformat()}",
                    text,
                )
            await self._send_chunks(discord_thread, f"**{label}**\n{text}")
            if item_id and imported_path is not None:
                imported.add(item_id)
                imported_path.write_text(
                    json.dumps(sorted(imported), indent=2) + "\n", encoding="utf-8"
                )
            return

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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Pam Discord agent bridge")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Private dotenv file containing DISCORD_BOT_TOKEN",
    )
    args = parser.parse_args(argv)
    load_dotenv(args.env_file)
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is missing; create a local .env from .env.example")
    config = load_config(args.config)
    _acquire_instance_lock(config.instance_lock_dir)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    PamDiscord(config).run(token, log_handler=None)


if __name__ == "__main__":
    main()
