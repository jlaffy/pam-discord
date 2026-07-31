from __future__ import annotations

import argparse
import atexit
import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import socket
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import discord
from dotenv import load_dotenv
from faster_whisper import WhisperModel

from .app_server import CodexAppServer, load_shared_sessions, save_shared_sessions
from .config import ChannelConfig, Config, load_config

LOG = logging.getLogger("pam_discord")
DISCORD_AGENT_INSTRUCTION = (
    "You are running through pam's unattended Discord interface. "
    "Use existing authenticated local command-line tools (for example gh for GitHub) when "
    "available. Request installation or connection of an app, connector, or plugin only when "
    "there is no suitable authenticated local tool or the user explicitly asks for that connector."
)
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"}
DELIVERABLE_EXTENSIONS = {
    ".csv", ".docx", ".gif", ".html", ".jpeg", ".jpg", ".md", ".pdf",
    ".png", ".ppt", ".pptx", ".svg", ".tsv", ".txt", ".webp", ".xls",
    ".xlsx", ".zip",
}


class ConnectorApprovalView(discord.ui.View):
    """Authorized-user-only response controls for a connector suggestion."""

    def __init__(self, allowed_user_ids: frozenset[int], *, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.allowed_user_ids = allowed_user_ids
        self.action = "decline"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.allowed_user_ids:
            return True
        await interaction.response.send_message(
            "Only an authorized pam user can approve this connection.", ephemeral=True
        )
        return False

    async def _finish(self, interaction: discord.Interaction, action: str) -> None:
        self.action = action
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        label = "approved" if action == "accept" else "declined"
        await interaction.response.edit_message(
            content=f"**pam** · Connector setup {label}.", view=self
        )
        self.stop()

    @discord.ui.button(label="Connect", style=discord.ButtonStyle.success)
    async def connect(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._finish(interaction, "accept")

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._finish(interaction, "decline")


class ConnectorOAuthView(ConnectorApprovalView):
    """OAuth link plus completion controls, intended for an authorized user's DM."""

    def __init__(
        self,
        allowed_user_ids: frozenset[int],
        url: str,
        *,
        timeout: float = 600,
    ) -> None:
        super().__init__(allowed_user_ids, timeout=timeout)
        self.remove_item(self.connect)
        self.add_item(
            discord.ui.Button(
                label="Open authorization page",
                style=discord.ButtonStyle.link,
                url=url,
            )
        )

    @discord.ui.button(label="Authorization complete", style=discord.ButtonStyle.success)
    async def complete(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._finish(interaction, "accept")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned[:100] or "audio"


def _clean_thread_title(value: str) -> str:
    title = re.sub(r"^[#*_`\"'\s]+|[#*_`\"'\s]+$", "", value)
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"^(?:title|thread title)\s*:\s*", "", title, flags=re.IGNORECASE)
    return title[:80].rstrip()


def _is_audio(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    return (
        content_type.startswith("audio/")
        or Path(attachment.filename).suffix.lower() in AUDIO_EXTENSIONS
    )


def _remote_project_command(command: str) -> tuple[str, Path] | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if (
        len(parts) != 4
        or [value.lower() for value in parts[:2]] != ["pam", "project"]
        or parts[2].lower() not in {"add", "connect", "create"}
    ):
        return None
    action = parts[2].lower()
    return (
        "create" if action == "create" else "connect",
        Path(parts[3]).expanduser().resolve(),
    )


def _allowed_project_roots(config: Config) -> set[Path]:
    if config.project_roots:
        return set(config.project_roots)
    workspaces = [item.workspace for item in config.guilds.values()]
    return {workspace.parent for workspace in workspaces}


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def _append_markdown(path: Path, heading: str, body: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"## {heading}\n\n{body.strip()}\n\n")


def _deliverable_paths(text: str, workspace: Path) -> list[Path]:
    candidates = re.findall(r"\]\(([^)\n]+)\)|`([^`\n]+)`", text)
    paths: list[Path] = []
    seen: set[Path] = set()
    for pair in candidates:
        raw = next((value for value in pair if value), "").strip().strip("<>")
        raw = re.sub(r":\d+(?::\d+)?$", "", raw)
        if not raw or "://" in raw:
            continue
        candidate = Path(raw).expanduser()
        candidate = candidate if candidate.is_absolute() else workspace / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if (
            resolved in seen
            or resolved.suffix.lower() not in DELIVERABLE_EXTENSIONS
            or not resolved.is_file()
            or not resolved.is_relative_to(workspace)
            or resolved.is_relative_to(workspace / ".pam")
        ):
            continue
        seen.add(resolved)
        paths.append(resolved)
    return paths


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


def _disable_session_polling(workspace: Path, codex_thread_id: str) -> None:
    sessions = _load_polled_sessions(workspace)
    if codex_thread_id not in sessions:
        return
    sessions.remove(codex_thread_id)
    _write_json(_polled_sessions_path(workspace), sorted(sessions))


def _recently_mirrored(
    cache: dict[tuple[str, str, str], float],
    key: tuple[str, str, str],
    now: float,
    *,
    window_seconds: float = 15,
) -> bool:
    expired = [item for item, timestamp in cache.items() if now - timestamp > window_seconds]
    for item in expired:
        cache.pop(item, None)
    if key in cache:
        return True
    cache[key] = now
    return False


def _acquire_instance_lock(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise SystemExit(
            f"pam already has an instance lock at {path}. "
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
        self._recent_mirrors: dict[tuple[str, str, str], float] = {}
        self._app_server = CodexAppServer(
            self.config.codex_app_server_url, self._handle_app_server_notification
        )
        self._link_watcher: asyncio.Task[None] | None = None
        self._session_catalog_synced = False
        self._project_setup_task: asyncio.Task[None] | None = None
        self._directory_channel_lock = asyncio.Lock()
        self._pending_discord_renames: dict[int, str] = {}
        self._linking_codex_threads: set[str] = set()
        self._last_catalog_sync = 0.0
        self._membership_synced_threads: set[int] = set()
        self._turn_typing: dict[str, object] = {}
        self._always_on_projects: dict[Path, ChannelConfig] = {}
        self._always_on_lock = asyncio.Lock()
        self._index_update_tasks: dict[Path, asyncio.Task[None]] = {}
        self._catalog_sync_lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        self.config.archive_dir.mkdir(parents=True, exist_ok=True)
        await self._app_server.start(self.config.codex_binary)
        self._link_watcher = asyncio.create_task(self._watch_link_requests())
        asyncio.create_task(self._warm_transcriber())

    async def close(self) -> None:
        if self._link_watcher is not None:
            self._link_watcher.cancel()
        await asyncio.gather(
            *(self._stop_turn_typing(thread_id) for thread_id in tuple(self._turn_typing)),
            return_exceptions=True,
        )
        for task in self._index_update_tasks.values():
            task.cancel()
        await self._app_server.close()
        await super().close()

    async def on_ready(self) -> None:
        LOG.info(
            "connected as %s; listening in %d project server(s) and %d channel override(s)",
            self.user,
            len(self.config.guilds),
            len(self.config.channels),
        )
        if not self._session_catalog_synced:
            self._session_catalog_synced = True
            self._last_catalog_sync = time.monotonic()
            asyncio.create_task(self._sync_project_session_catalogs())

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

    def _all_channel_configs(self) -> list[ChannelConfig]:
        unique: dict[tuple[Path, Path], ChannelConfig] = {}
        for item in (
            *self.config.guilds.values(),
            *self.config.channels.values(),
            *self._always_on_projects.values(),
        ):
            state = item.session_state_dir or item.workspace
            unique[(item.workspace, state)] = item
        return list(unique.values())

    @staticmethod
    def _session_state_workspace(channel_config: ChannelConfig) -> Path:
        return channel_config.session_state_dir or channel_config.workspace

    def _load_channel_sessions(self, channel_config: ChannelConfig) -> dict[str, int]:
        return load_shared_sessions(self._session_state_workspace(channel_config))

    def _save_channel_sessions(
        self, channel_config: ChannelConfig, sessions: dict[str, int]
    ) -> None:
        save_shared_sessions(self._session_state_workspace(channel_config), sessions)

    def _detect_project_root(self, cwd: Path) -> Path | None:
        cwd = cwd.resolve()
        roots = sorted(
            _allowed_project_roots(self.config),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        approved = next(
            (root for root in roots if cwd == root or cwd.is_relative_to(root)),
            None,
        )
        if approved is None:
            return None
        candidate = cwd
        while candidate != approved.parent:
            if (candidate / ".git").exists():
                return candidate
            if candidate == approved:
                break
            candidate = candidate.parent
        if cwd == approved:
            return approved
        relative = cwd.relative_to(approved)
        return approved / relative.parts[0]

    def _always_on_project_config(self, cwd: Path) -> ChannelConfig | None:
        if self.config.always_on_guild_id is None or self.config.always_on_state_dir is None:
            return None
        project_root = self._detect_project_root(cwd)
        if project_root is None:
            return None
        existing = self._always_on_projects.get(project_root)
        if existing is not None:
            return existing
        digest = hashlib.sha256(str(project_root).encode()).hexdigest()[:10]
        project_state = (
            self.config.always_on_state_dir
            / "projects"
            / f"{_safe_name(project_root.name).lower()}-{digest}"
        )
        project_state.mkdir(parents=True, exist_ok=True)
        config = ChannelConfig(
            workspace=project_root,
            project_root=project_root,
            run_codex=True,
            instruction_prefix="Follow this project's instructions.",
            project_record_dir=project_state / "conversations",
            session_state_dir=project_state,
        )
        self._always_on_projects[project_root] = config
        return config

    async def _always_on_forum(
        self, channel_config: ChannelConfig
    ) -> discord.ForumChannel | None:
        guild_id = self.config.always_on_guild_id
        if guild_id is None or channel_config.session_state_dir is None:
            return None
        existing = next(
            (
                self.get_channel(channel_id)
                for channel_id, item in self.config.channels.items()
                if item is channel_config
            ),
            None,
        )
        if isinstance(existing, discord.ForumChannel):
            return existing
        guild = self.get_guild(guild_id)
        if guild is None:
            return None
        async with self._always_on_lock:
            existing = next(
                (
                    self.get_channel(channel_id)
                    for channel_id, item in self.config.channels.items()
                    if item is channel_config
                ),
                None,
            )
            if isinstance(existing, discord.ForumChannel):
                return existing
            base = re.sub(r"[^a-z0-9-]+", "-", channel_config.workspace.name.lower())
            name = base.strip("-")[:90] or "project"
            topic = f"Pam sessions in {channel_config.workspace}"
            forum = next(
                (
                    item
                    for item in guild.forums
                    if item.name == name and item.topic == topic
                ),
                None,
            )
            if forum is None and any(item.name == name for item in guild.forums):
                digest = hashlib.sha256(
                    str(channel_config.workspace).encode()
                ).hexdigest()[:6]
                name = f"{name[:83]}-{digest}"
                forum = discord.utils.get(guild.forums, name=name)
            if forum is None:
                forum = await guild.create_forum(
                    name,
                    topic=topic,
                    reason="Pam always-on project discovery",
                )
            self.config.channels[forum.id] = channel_config
            return forum

    def _schedule_directory_index(self, channel_config: ChannelConfig) -> None:
        if channel_config.session_state_dir is None:
            return
        existing = self._index_update_tasks.get(channel_config.workspace)
        if existing is not None and not existing.done():
            existing.cancel()
        self._index_update_tasks[channel_config.workspace] = asyncio.create_task(
            self._update_directory_index_after_delay(channel_config)
        )

    async def _update_directory_index_after_delay(
        self, channel_config: ChannelConfig
    ) -> None:
        try:
            await asyncio.sleep(2)
            await self._update_directory_index(channel_config)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("failed to update directory index for %s", channel_config.workspace)

    def _directory_index_text(self, channel_config: ChannelConfig) -> str:
        records = channel_config.project_record_dir
        grouped: dict[str, list[tuple[str, int]]] = {}
        if records is not None and records.exists():
            for metadata_path in records.glob("*/metadata.json"):
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    cwd = Path(str(metadata.get("workspace") or channel_config.workspace))
                    relative = (
                        "root"
                        if cwd == channel_config.workspace
                        else str(cwd.relative_to(channel_config.workspace))
                    )
                    thread_id = int(metadata["discord_thread_id"])
                    title = str(metadata.get("title") or f"Session {thread_id}")
                except (KeyError, OSError, ValueError, json.JSONDecodeError):
                    continue
                grouped.setdefault(relative, []).append((title, thread_id))

        tree: dict[str, object] = {}
        counts: dict[tuple[str, ...], int] = {}
        for relative, sessions in grouped.items():
            parts = () if relative == "root" else tuple(Path(relative).parts)
            node = tree
            counts[parts] = len(sessions)
            for part in parts:
                child = node.setdefault(part, {})
                if not isinstance(child, dict):
                    break
                node = child

        lines = [f"{channel_config.workspace.name}/"]
        if "root" in grouped:
            lines.append(f"├── root/  {len(grouped['root'])} session(s)")

        def render(node: dict[str, object], prefix: str = "") -> None:
            items = sorted(node.items())
            for index, (name, child) in enumerate(items):
                last = index == len(items) - 1
                path_prefix = prefix + ("└── " if last else "├── ")
                lines.append(f"{path_prefix}{name}/")
                if isinstance(child, dict):
                    render(child, prefix + ("    " if last else "│   "))

        render(tree)
        if len(lines) == 1:
            lines.append("└── No sessions discovered yet")

        body = [
            f"**Pam directory index · `{channel_config.workspace}`**",
            "",
            "```text",
            *lines,
            "```",
            "",
        ]
        guild_id = self.config.always_on_guild_id
        for relative, sessions in sorted(grouped.items()):
            body.append(f"**`{relative}`**")
            for title, thread_id in sessions[-5:]:
                body.append(
                    f"• [{title}](https://discord.com/channels/{guild_id}/{thread_id})"
                )
            body.append("")
        return "\n".join(body)[:3900]

    async def _update_directory_index(self, channel_config: ChannelConfig) -> None:
        forum = await self._always_on_forum(channel_config)
        state_dir = channel_config.session_state_dir
        if forum is None or state_dir is None:
            return
        text = await asyncio.to_thread(self._directory_index_text, channel_config)
        state_path = state_dir / "directory-index.json"
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else {}
        )
        thread_id = state.get("thread_id")
        message_id = state.get("message_id")
        if isinstance(thread_id, int) and isinstance(message_id, int):
            thread = self.get_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                try:
                    thread = await self.fetch_channel(thread_id)
                except discord.HTTPException:
                    thread = None
            if isinstance(thread, discord.Thread):
                try:
                    message = await thread.fetch_message(message_id)
                    await message.edit(content=text)
                    return
                except discord.HTTPException:
                    pass
        created = await forum.create_thread(
            name="📁 Directory index",
            content=text,
            auto_archive_duration=10080,
        )
        _write_json(
            state_path,
            {"thread_id": created.thread.id, "message_id": created.message.id},
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author.id not in self.config.allowed_user_ids:
            return
        channel_config = self._channel_config(message.channel)
        if channel_config is None:
            return
        project_command = _remote_project_command(message.content.strip())
        if project_command is not None:
            action, project_path = project_command
            await self._start_remote_project_setup(
                message,
                project_path,
                create=action == "create",
            )
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

    async def on_thread_update(
        self, before: discord.Thread, after: discord.Thread
    ) -> None:
        if before.name == after.name:
            return
        expected = self._pending_discord_renames.get(after.id)
        if expected == after.name:
            self._pending_discord_renames.pop(after.id, None)
            return
        codex_thread_id = self._codex_thread_for_discord(after.id)
        if codex_thread_id is None:
            return
        title = _clean_thread_title(after.name)
        if not title:
            return
        try:
            await self._app_server.request(
                "thread/name/set", {"threadId": codex_thread_id, "name": title}
            )
        except Exception:
            LOG.exception("failed to rename Codex conversation %s", codex_thread_id)

    async def _start_remote_project_setup(
        self,
        message: discord.Message,
        workspace: Path,
        *,
        create: bool = False,
    ) -> None:
        if self._project_setup_task is not None and not self._project_setup_task.done():
            await message.reply(
                "pam is already connecting another project. Finish that setup first.",
                mention_author=False,
            )
            return
        if create and workspace.exists():
            await message.reply(
                f"That path already exists: `{workspace}`. Use `pam project add` instead.",
                mention_author=False,
            )
            return
        if not create and not workspace.is_dir():
            await message.reply(
                f"That directory does not exist: `{workspace}`", mention_author=False
            )
            return
        if any(item.workspace == workspace for item in self.config.guilds.values()):
            await message.reply(
                f"That project is already connected: `{workspace}`", mention_author=False
            )
            return
        roots = _allowed_project_roots(self.config)
        if not roots or not any(workspace.is_relative_to(root) for root in roots):
            allowed = ", ".join(f"`{root}`" for root in sorted(roots))
            await message.reply(
                f"That directory is outside pam's allowed project roots: {allowed}",
                mention_author=False,
            )
            return
        if create:
            if not workspace.parent.is_dir():
                await message.reply(
                    f"The parent directory does not exist: `{workspace.parent}`",
                    mention_author=False,
                )
                return
            workspace.mkdir()
        if self.user is None:
            await message.reply("pam is not connected to Discord yet.", mention_author=False)
            return
        from .setup import _discord_install_url

        known_guild_ids = {guild.id for guild in self.guilds}
        await message.reply(
            "\n".join(
                [
                    f"Connecting `{workspace}`.",
                    "",
                    "1. [Create a Discord server](https://discord.com/channels/@me) "
                    f"named **{workspace.name}**.",
                    f"2. [Add pam to the new server]({_discord_install_url(str(self.user.id))}).",
                    "",
                    "pam will detect it and finish setup automatically. "
                    "This request expires in 10 minutes.",
                ]
            ),
            mention_author=False,
        )
        self._project_setup_task = asyncio.create_task(
            self._finish_remote_project_setup(message, workspace, known_guild_ids)
        )

    async def _finish_remote_project_setup(
        self,
        message: discord.Message,
        workspace: Path,
        known_guild_ids: set[int],
    ) -> None:
        try:
            for _ in range(300):
                candidates = [
                    guild
                    for guild in self.guilds
                    if guild.id not in known_guild_ids
                    and guild.owner_id == message.author.id
                ]
                guild = next(
                    (item for item in candidates if item.name == workspace.name),
                    candidates[0] if len(candidates) == 1 else None,
                )
                if guild is not None:
                    await self._configure_remote_project(message, workspace, guild)
                    return
                await asyncio.sleep(2)
            await message.reply(
                "Project setup expired before pam detected the new server. "
                f"Send `pam project add {workspace}` to try again.",
                mention_author=False,
            )
        except Exception:
            LOG.exception("failed to connect remote project %s", workspace)
            await message.reply(
                "pam couldn't finish connecting that project. Check `pam service logs`.",
                mention_author=False,
            )

    async def _configure_remote_project(
        self,
        message: discord.Message,
        workspace: Path,
        guild: discord.Guild,
    ) -> None:
        if self.config.config_path is None:
            raise RuntimeError("pam configuration path is unavailable")
        member = guild.me
        if member is not None:
            await member.edit(nick="pam")
        channel = discord.utils.get(guild.text_channels, name="general")
        if channel is None:
            channel = await guild.create_text_channel(
                "general", topic=f"pam workspace for {workspace}"
            )
        from .setup import _configure_project_archive_git, _project_config_block

        with self.config.config_path.open("a", encoding="utf-8") as handle:
            handle.write(_project_config_block(channel.id, guild.id, workspace))
        _configure_project_archive_git(workspace, ignore=True)
        channel_config = ChannelConfig(
            workspace=workspace,
            run_codex=True,
            instruction_prefix="Follow this project's instructions.",
            project_record_dir=workspace / ".pam" / "conversations",
            project_root=workspace,
        )
        self.config.guilds[guild.id] = channel_config
        self.config.channels[channel.id] = channel_config
        await channel.send(
            f"**pam** · `{workspace}` is connected. Send a message here to start a conversation."
        )
        await message.reply(
            f"Project connected: {channel.jump_url}", mention_author=False
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
        await self._add_authorized_thread_members(
            thread, message.guild, exclude={message.author.id}
        )

    async def _add_authorized_thread_members(
        self,
        thread: discord.Thread,
        guild: discord.Guild,
        *,
        exclude: set[int] | None = None,
    ) -> None:
        for user_id in self.config.allowed_user_ids:
            if exclude is not None and user_id in exclude:
                continue
            try:
                member = guild.get_member(user_id)
                if member is None:
                    member = await guild.fetch_member(user_id)
                await thread.add_user(member)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                LOG.warning("could not add authorized user %s to thread %s", user_id, thread.id)
        self._membership_synced_threads.add(thread.id)

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
                part
                for part in (
                    DISCORD_AGENT_INSTRUCTION,
                    channel_config.instruction_prefix,
                    prompt,
                )
                if part
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

            shared_thread_id = self._shared_codex_thread(channel_config, thread.id)
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
                        "sandboxPolicy": (
                            {"type": "dangerFullAccess"}
                            if self.config.codex_full_access
                            else None
                        ),
                    },
                )
                return

            if channel_config.run_codex:
                await self._start_discord_codex_session(
                    channel_config,
                    thread,
                    message.id,
                    agent_prompt,
                    conversation_dir,
                    project_record[0] if project_record is not None else None,
                )

    async def _start_discord_codex_session(
        self,
        channel_config: ChannelConfig,
        discord_thread: discord.Thread,
        discord_message_id: int,
        agent_prompt: str,
        conversation_dir: Path,
        project_conversation_dir: Path | None,
    ) -> str:
        result = await self._app_server.request(
            "thread/start",
            {
                "cwd": str(channel_config.workspace),
                "approvalPolicy": "never",
                "serviceName": "pam_discord",
            },
        )
        value = result.get("thread") if isinstance(result, dict) else None
        codex_thread_id = str(value.get("id") or "") if isinstance(value, dict) else ""
        if not codex_thread_id:
            raise RuntimeError("Codex app-server started a conversation without an ID")

        # Save the relationship before starting work. Notifications and the session
        # catalog can now recognize that this conversation already has a Discord thread.
        sessions = self._load_channel_sessions(channel_config)
        sessions[codex_thread_id] = discord_thread.id
        self._save_channel_sessions(channel_config, sessions)
        _write_json(
            conversation_dir / "state.json",
            {
                "codex_session_id": codex_thread_id,
                "workspace": str(channel_config.workspace),
            },
        )
        if project_conversation_dir is not None:
            metadata_path = project_conversation_dir / "metadata.json"
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["codex_thread_id"] = codex_thread_id
                metadata["title"] = str(
                    getattr(discord_thread, "name", f"Session {discord_thread.id}")
                )
                _write_json(metadata_path, metadata)
        self._schedule_directory_index(channel_config)

        turn_params: dict[str, object] = {
            "threadId": codex_thread_id,
            "input": [{"type": "text", "text": agent_prompt}],
            "clientUserMessageId": f"discord:{discord_message_id}",
            "approvalPolicy": "never",
        }
        if self.config.codex_full_access:
            turn_params["sandboxPolicy"] = {"type": "dangerFullAccess"}
        await self._app_server.request("turn/start", turn_params)
        return codex_thread_id

    async def _name_discord_started_session(
        self,
        discord_thread: discord.Thread,
        codex_thread_id: str,
        prompt: str,
        workspace: Path,
        record_dir: Path,
    ) -> None:
        """Give a Discord-started session one useful name in both clients."""
        try:
            result = await self._app_server.request(
                "thread/read", {"threadId": codex_thread_id, "includeTurns": False}
            )
            value = result.get("thread") if isinstance(result, dict) else None
            title = (
                _clean_thread_title(str(value.get("name") or ""))
                if isinstance(value, dict)
                else ""
            )
            if not title:
                title = await asyncio.to_thread(
                    self._generate_thread_title,
                    prompt,
                    workspace,
                    record_dir,
                )
            if not title:
                return
            await self._app_server.request(
                "thread/name/set", {"threadId": codex_thread_id, "name": title}
            )
            if discord_thread.name != title:
                await self._edit_discord_thread_name(discord_thread, title)
        except Exception:
            LOG.exception("failed to name shared session %s", codex_thread_id)

    def _generate_thread_title(
        self,
        prompt: str,
        workspace: Path,
        record_dir: Path,
    ) -> str:
        title_path = record_dir / "codex-title.txt"
        instruction = (
            "Write a concise title for this coding-agent conversation. "
            "Return only the title, with no quotation marks or punctuation wrapper. "
            "Use 3 to 8 words and at most 80 characters.\n\n"
            f"Conversation request:\n{prompt[:6000]}"
        )
        command = [
            self.config.codex_binary,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-C",
            str(workspace),
            "-o",
            str(title_path),
            "--",
            instruction,
        ]
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=min(self.config.codex_timeout_seconds, 120),
            check=False,
            env={key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"},
        )
        if result.returncode != 0:
            LOG.warning("Codex title generation failed: %s", result.stderr.strip()[-500:])
            return ""
        value = title_path.read_text(encoding="utf-8") if title_path.exists() else result.stdout
        return _clean_thread_title(value)

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

    def _shared_codex_thread(
        self, channel_config: ChannelConfig, discord_thread_id: int
    ) -> str | None:
        for codex_thread_id, mapped_discord_id in self._load_channel_sessions(
            channel_config
        ).items():
            if mapped_discord_id == discord_thread_id:
                return codex_thread_id
        return None

    def _codex_thread_for_discord(self, discord_thread_id: int) -> str | None:
        seen: set[Path] = set()
        for channel_config in self._all_channel_configs():
            state = self._session_state_workspace(channel_config)
            if state in seen:
                continue
            seen.add(state)
            codex_thread_id = self._shared_codex_thread(
                channel_config, discord_thread_id
            )
            if codex_thread_id is not None:
                return codex_thread_id
        return None

    async def _edit_discord_thread_name(
        self, discord_thread: discord.Thread, title: str
    ) -> None:
        cleaned = _clean_thread_title(title)
        if not cleaned or discord_thread.name == cleaned:
            return
        self._pending_discord_renames[discord_thread.id] = cleaned
        try:
            await discord_thread.edit(name=cleaned)
        except Exception:
            self._pending_discord_renames.pop(discord_thread.id, None)
            raise

    def _workspace_config_for_cwd(self, cwd: Path) -> ChannelConfig | None:
        discovered = self._always_on_project_config(cwd)
        matches = [
            item
            for item in self._all_channel_configs()
            if cwd == item.workspace or cwd.is_relative_to(item.workspace)
        ]
        if discovered is not None:
            matches.append(discovered)
        return max(matches, key=lambda item: len(item.workspace.parts), default=None)

    async def _handle_app_server_notification(self, event: dict[str, object]) -> None:
        method = event.get("method")
        params = event.get("params")
        if not isinstance(params, dict):
            return
        if method == "mcpServer/elicitation/request":
            request_id = event.get("id")
            metadata = params.get("_meta")
            if (
                isinstance(request_id, int)
                and isinstance(metadata, dict)
                and metadata.get("codex_approval_kind") == "tool_suggestion"
            ):
                codex_thread_id = str(params.get("threadId") or "")
                discord_thread_id = self._discord_thread_for_codex(codex_thread_id)
                action = "decline"
                if discord_thread_id is not None:
                    discord_thread = self.get_channel(discord_thread_id)
                    if not isinstance(discord_thread, discord.Thread):
                        try:
                            discord_thread = await self.fetch_channel(discord_thread_id)
                        except discord.HTTPException:
                            discord_thread = None
                    if isinstance(discord_thread, discord.Thread):
                        connector = str(params.get("serverName") or "external connector")
                        view = ConnectorApprovalView(self.config.allowed_user_ids)
                        await discord_thread.send(
                            f"**pam** · Codex wants to connect **{connector}**.\n"
                            f"{str(params.get('message') or '').strip()}\n\n"
                            "Pam will use an existing authenticated local tool instead whenever "
                            "one is available. Connect only if this external connector is needed.",
                            view=view,
                        )
                        timed_out = await view.wait()
                        action = "decline" if timed_out else view.action
                LOG.info(
                    "%s connector request for Discord session: %s",
                    action,
                    params.get("message"),
                )
                await self._app_server.respond(request_id, {"action": action})
            elif isinstance(request_id, int) and params.get("mode") == "url":
                action = "decline"
                url = str(params.get("url") or "")
                if url.startswith("https://"):
                    for user_id in self.config.allowed_user_ids:
                        try:
                            user = self.get_user(user_id) or await self.fetch_user(user_id)
                            view = ConnectorOAuthView(
                                self.config.allowed_user_ids,
                                url,
                            )
                            await user.send(
                                f"**pam** · **{str(params.get('serverName') or 'A connector')}** "
                                "needs authorization. Open the authorization page, complete the "
                                "provider's sign-in, then return here and press "
                                "**Authorization complete**.",
                                view=view,
                            )
                            timed_out = await view.wait()
                            action = "decline" if timed_out else view.action
                            break
                        except discord.HTTPException:
                            LOG.exception(
                                "could not DM connector authorization to user %s", user_id
                            )
                await self._app_server.respond(request_id, {"action": action})
            return
        if method == "turn/started":
            await self._start_turn_typing(str(params.get("threadId") or ""))
            await asyncio.to_thread(
                self._record_app_server_event,
                str(params.get("threadId") or ""),
                event,
            )
        elif method in {"turn/completed", "turn/failed", "turn/cancelled"}:
            codex_thread_id = str(params.get("threadId") or "")
            await self._stop_turn_typing(codex_thread_id)
            await asyncio.to_thread(
                self._record_app_server_event, codex_thread_id, event
            )
        elif method == "thread/started":
            thread_value = params.get("thread")
            if isinstance(thread_value, dict):
                if str(thread_value.get("name") or thread_value.get("preview") or "").strip():
                    await self._link_started_codex_thread(thread_value)
                else:
                    asyncio.create_task(
                        self._link_when_conversation_is_materialized(
                            str(thread_value.get("id") or "")
                        )
                    )
                await asyncio.to_thread(
                    self._record_app_server_event,
                    str(thread_value.get("id") or ""),
                    event,
                )
        elif method == "item/completed":
            codex_thread_id = str(params.get("threadId") or "")
            await asyncio.to_thread(self._stop_polling_live_session, codex_thread_id)
            await asyncio.to_thread(
                self._record_app_server_event, codex_thread_id, event
            )
            await self._mirror_completed_codex_item(params)
        elif method == "thread/name/updated":
            codex_thread_id = str(params.get("threadId") or "")
            title = _clean_thread_title(
                str(params.get("threadName") or params.get("name") or "")
            )
            discord_thread_id = await asyncio.to_thread(
                self._discord_thread_for_codex, codex_thread_id
            )
            if discord_thread_id is not None and title:
                discord_thread = self.get_channel(discord_thread_id)
                if not isinstance(discord_thread, discord.Thread):
                    try:
                        discord_thread = await self.fetch_channel(discord_thread_id)
                    except discord.HTTPException:
                        discord_thread = None
                if isinstance(discord_thread, discord.Thread):
                    await self._edit_discord_thread_name(discord_thread, title)
            await asyncio.to_thread(
                self._record_app_server_event, codex_thread_id, event
            )
        else:
            await asyncio.to_thread(
                self._record_app_server_event,
                str(params.get("threadId") or ""),
                event,
            )

    async def _start_turn_typing(self, codex_thread_id: str) -> None:
        if not codex_thread_id or codex_thread_id in self._turn_typing:
            return
        discord_thread_id = await asyncio.to_thread(
            self._discord_thread_for_codex, codex_thread_id
        )
        if discord_thread_id is None:
            return
        thread = self.get_channel(discord_thread_id)
        if not isinstance(thread, discord.Thread):
            try:
                thread = await self.fetch_channel(discord_thread_id)
            except discord.HTTPException:
                return
        if not isinstance(thread, discord.Thread):
            return
        typing = thread.typing()
        await typing.__aenter__()
        self._turn_typing[codex_thread_id] = typing

    async def _stop_turn_typing(self, codex_thread_id: str) -> None:
        typing = self._turn_typing.pop(codex_thread_id, None)
        if typing is not None:
            await typing.__aexit__(None, None, None)  # type: ignore[attr-defined]

    def _discord_thread_for_codex(self, codex_thread_id: str) -> int | None:
        for channel_config in self._all_channel_configs():
            discord_thread_id = self._load_channel_sessions(channel_config).get(
                codex_thread_id
            )
            if discord_thread_id is not None:
                return discord_thread_id
        return None

    async def _link_when_conversation_is_materialized(self, codex_thread_id: str) -> None:
        if not codex_thread_id:
            return
        for _ in range(60):
            try:
                result = await self._app_server.request(
                    "thread/read",
                    {"threadId": codex_thread_id, "includeTurns": False},
                )
            except Exception:
                LOG.exception(
                    "failed waiting for Codex conversation %s", codex_thread_id
                )
                return
            value = result.get("thread") if isinstance(result, dict) else None
            if isinstance(value, dict) and str(
                value.get("name") or value.get("preview") or ""
            ).strip():
                try:
                    await self._link_started_codex_thread(value)
                except Exception:
                    LOG.exception(
                        "failed to link materialized Codex conversation %s",
                        codex_thread_id,
                    )
                return
            await asyncio.sleep(0.5)

    def _stop_polling_live_session(self, codex_thread_id: str) -> None:
        if not codex_thread_id:
            return
        for channel_config in self._all_channel_configs():
            state = self._session_state_workspace(channel_config)
            if codex_thread_id in self._load_channel_sessions(channel_config):
                _disable_session_polling(state, codex_thread_id)
                return

    def _record_app_server_event(
        self, codex_thread_id: str, event: dict[str, object]
    ) -> None:
        if not codex_thread_id:
            return
        for channel_config in self._all_channel_configs():
            discord_thread_id = self._load_channel_sessions(channel_config).get(
                codex_thread_id
            )
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
                                    self._session_state_workspace(channel_config), thread_id
                                )
                                await self._import_codex_history(thread_id)
                    path.unlink()
                except Exception:
                    LOG.exception("failed to process pam link request %s", path)
            await self._sync_shared_sessions()
            catalog_interval = 2 if self.config.always_on_guild_id is not None else 60
            if time.monotonic() - self._last_catalog_sync >= catalog_interval:
                self._last_catalog_sync = time.monotonic()
                asyncio.create_task(self._sync_project_session_catalogs())
            await asyncio.sleep(2)

    async def _sync_shared_sessions(self) -> None:
        """Import turns written by Codex clients that predate `pam codex`."""
        seen: set[str] = set()
        for channel_config in self._all_channel_configs():
            for codex_thread_id in _load_polled_sessions(
                self._session_state_workspace(channel_config)
            ):
                if codex_thread_id in seen:
                    continue
                seen.add(codex_thread_id)
                try:
                    await self._import_codex_history(codex_thread_id)
                except Exception:
                    LOG.exception("failed to sync Codex conversation %s", codex_thread_id)

    async def _sync_project_session_catalogs(self) -> None:
        """Create missing Discord threads for all saved sessions in connected projects."""
        if self._catalog_sync_lock.locked():
            return
        async with self._catalog_sync_lock:
            state_path = (
                self.config.always_on_state_dir / "catalog-checkpoint.json"
                if self.config.always_on_state_dir is not None
                else None
            )
            checkpoint: dict[str, str] = {}
            initialized = False
            if state_path is not None and state_path.exists():
                try:
                    raw_state = await asyncio.to_thread(
                        state_path.read_text, encoding="utf-8"
                    )
                    state = json.loads(raw_state)
                    checkpoint = {
                        str(key): str(value)
                        for key, value in dict(state.get("sessions", {})).items()
                    }
                    initialized = bool(state.get("initialized"))
                except (OSError, ValueError, json.JSONDecodeError):
                    LOG.warning("ignoring invalid catalog checkpoint %s", state_path)

            cursor: str | None = None
            catalog_changed = False
            while True:
                params: dict[str, object] = {
                    "sourceKinds": ["cli", "exec", "appServer"],
                    "archived": False,
                    "limit": 100,
                    "sortKey": "recency_at",
                    "sortDirection": "desc",
                }
                if cursor is not None:
                    params["cursor"] = cursor
                try:
                    result = await self._app_server.request("thread/list", params)
                except Exception:
                    LOG.exception("failed to list Codex conversations")
                    return
                if not isinstance(result, dict):
                    return
                for value in result.get("data", []):
                    if not isinstance(value, dict):
                        continue
                    codex_thread_id = str(value.get("id") or "")
                    if not codex_thread_id:
                        continue
                    fingerprint = "|".join(
                        str(value.get(key) or "")
                        for key in ("updatedAt", "updated_at", "name", "preview")
                    )
                    if initialized and checkpoint.get(codex_thread_id) == fingerprint:
                        continue
                    try:
                        await self._link_started_codex_thread(value)
                        channel_config = self._workspace_config_for_cwd(
                            Path(str(value.get("cwd") or "")).resolve()
                        )
                        if (
                            initialized
                            and channel_config is not None
                            and channel_config.session_state_dir is not None
                            and codex_thread_id
                            in self._load_channel_sessions(channel_config)
                        ):
                            await self._import_codex_history(codex_thread_id)
                        checkpoint[codex_thread_id] = fingerprint
                        catalog_changed = True
                    except Exception:
                        LOG.exception(
                            "failed to mirror Codex session %s", codex_thread_id
                        )
                next_cursor = result.get("nextCursor")
                if initialized or not isinstance(next_cursor, str) or not next_cursor:
                    break
                cursor = next_cursor
            if state_path is not None:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(
                    _write_json,
                    state_path,
                    {"initialized": True, "sessions": checkpoint},
                )
                if catalog_changed:
                    for channel_config in self._always_on_projects.values():
                        self._schedule_directory_index(channel_config)

    async def _link_started_codex_thread(self, value: dict[str, object]) -> None:
        codex_thread_id = str(value.get("id") or "")
        if not codex_thread_id or codex_thread_id in self._linking_codex_threads:
            return
        self._linking_codex_threads.add(codex_thread_id)
        try:
            await self._link_started_codex_thread_once(value)
        finally:
            self._linking_codex_threads.discard(codex_thread_id)

    async def _link_started_codex_thread_once(self, value: dict[str, object]) -> None:
        codex_thread_id = str(value.get("id") or "")
        cwd_value = value.get("cwd")
        if not codex_thread_id or not isinstance(cwd_value, str):
            return
        cwd = Path(cwd_value).resolve()
        channel_config = self._workspace_config_for_cwd(cwd)
        if channel_config is None:
            return
        sessions = self._load_channel_sessions(channel_config)
        if codex_thread_id in sessions:
            await self._ensure_authorized_thread_members(sessions[codex_thread_id])
            return
        originating_thread = (
            None
            if channel_config.session_state_dir is not None
            else await self._originating_discord_thread(codex_thread_id)
        )
        if originating_thread is not None:
            sessions[codex_thread_id] = originating_thread.id
            self._save_channel_sessions(channel_config, sessions)
            if channel_config.project_record_dir is not None:
                metadata_path = (
                    channel_config.project_record_dir
                    / str(originating_thread.id)
                    / "metadata.json"
                )
                if metadata_path.exists():
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    metadata["codex_thread_id"] = codex_thread_id
                    _write_json(metadata_path, metadata)
            await self._ensure_authorized_thread_members(originating_thread.id)
            return
        forum = await self._always_on_forum(channel_config)
        parent: discord.TextChannel | discord.ForumChannel | None = forum
        if parent is None:
            parent = await self._conversation_parent_channel(channel_config, cwd)
        if not isinstance(parent, (discord.TextChannel, discord.ForumChannel)):
            LOG.warning("no default Discord channel for Codex thread %s", codex_thread_id)
            return
        preview = str(value.get("name") or value.get("preview") or "").strip()
        relative = (
            "root"
            if cwd == channel_config.workspace
            else str(cwd.relative_to(channel_config.workspace))
        )
        title_text = re.sub(r"\s+", " ", preview) or f"Codex {codex_thread_id[:8]}"
        title = f"[{relative}] {title_text}"[:80]
        if isinstance(parent, discord.ForumChannel):
            created = await parent.create_thread(
                name=title,
                auto_archive_duration=1440,
                content=(
                    "**pam** · Shared terminal and Discord Codex session connected.\n"
                    f"**Project:** `{channel_config.workspace}`\n"
                    f"**Working directory:** `{cwd}`"
                ),
            )
            discord_thread = created.thread
        else:
            discord_thread = await parent.create_thread(
                name=title,
                auto_archive_duration=1440,
                type=discord.ChannelType.public_thread,
            )
        await self._add_authorized_thread_members(
            discord_thread,
            parent.guild,
        )
        sessions[codex_thread_id] = discord_thread.id
        self._save_channel_sessions(channel_config, sessions)
        if channel_config.project_record_dir is not None:
            conversation_dir = channel_config.project_record_dir / str(discord_thread.id)
            conversation_dir.mkdir(parents=True, exist_ok=True)
            _write_json(
                conversation_dir / "metadata.json",
                {
                    "codex_thread_id": codex_thread_id,
                    "discord_thread_id": discord_thread.id,
                    "discord_parent_channel_id": parent.id,
                    "workspace": str(cwd),
                    "project_root": str(channel_config.workspace),
                    "title": title,
                    "created_at": datetime.now(UTC).isoformat(),
                    "source": "terminal",
                },
            )
        if not isinstance(parent, discord.ForumChannel):
            await discord_thread.send(
                "**pam** · Shared terminal and Discord Codex session connected."
            )
        await self._import_codex_history(codex_thread_id)
        self._schedule_directory_index(channel_config)
        if not str(value.get("name") or "").strip() and preview:
            asyncio.create_task(
                self._name_terminal_started_session(
                    discord_thread,
                    codex_thread_id,
                    preview,
                    cwd,
                )
            )

    async def _originating_discord_thread(
        self, codex_thread_id: str
    ) -> discord.Thread | None:
        """Recover the Discord thread that started a not-yet-mapped Codex session."""
        try:
            result = await self._app_server.request(
                "thread/read", {"threadId": codex_thread_id, "includeTurns": True}
            )
        except Exception:
            LOG.exception("failed to inspect Codex session %s before linking", codex_thread_id)
            raise
        thread = result.get("thread") if isinstance(result, dict) else None
        turns = thread.get("turns", []) if isinstance(thread, dict) else []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            for item in turn.get("items", []):
                if not isinstance(item, dict) or item.get("type") != "userMessage":
                    continue
                client_id = str(item.get("clientId") or "")
                if not client_id.startswith("discord:"):
                    continue
                try:
                    discord_id = int(client_id.removeprefix("discord:"))
                except ValueError:
                    continue
                candidate = self.get_channel(discord_id)
                if not isinstance(candidate, discord.Thread):
                    try:
                        candidate = await self.fetch_channel(discord_id)
                    except (discord.HTTPException, discord.InvalidData):
                        continue
                if isinstance(candidate, discord.Thread):
                    return candidate
        return None

    async def _name_terminal_started_session(
        self,
        discord_thread: discord.Thread,
        codex_thread_id: str,
        preview: str,
        workspace: Path,
    ) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="pam-title-") as temporary:
                title = await asyncio.to_thread(
                    self._generate_thread_title,
                    preview,
                    workspace,
                    Path(temporary),
                )
            if not title:
                return
            await self._app_server.request(
                "thread/name/set", {"threadId": codex_thread_id, "name": title}
            )
            await self._edit_discord_thread_name(discord_thread, title)
        except Exception:
            LOG.exception("failed to name terminal-started session %s", codex_thread_id)

    async def _ensure_authorized_thread_members(self, discord_thread_id: int) -> None:
        if discord_thread_id in self._membership_synced_threads:
            return
        thread = self.get_channel(discord_thread_id)
        if not isinstance(thread, discord.Thread):
            try:
                thread = await self.fetch_channel(discord_thread_id)
            except discord.HTTPException:
                return
        if isinstance(thread, discord.Thread):
            await self._add_authorized_thread_members(thread, thread.guild)

    async def _conversation_parent_channel(
        self, project_config: ChannelConfig, cwd: Path
    ) -> discord.TextChannel | None:
        project_root = project_config.workspace
        existing = next(
            (
                self.get_channel(channel_id)
                for channel_id, item in self.config.channels.items()
                if item.workspace == cwd
            ),
            None,
        )
        if isinstance(existing, discord.TextChannel):
            return existing
        if cwd == project_root:
            return None
        async with self._directory_channel_lock:
            existing = next(
                (
                    self.get_channel(channel_id)
                    for channel_id, item in self.config.channels.items()
                    if item.workspace == cwd
                ),
                None,
            )
            if isinstance(existing, discord.TextChannel):
                return existing
            guild_id = next(
                (
                    guild_id
                    for guild_id, item in self.config.guilds.items()
                    if item.workspace == project_root
                ),
                None,
            )
            guild = self.get_guild(guild_id) if guild_id is not None else None
            if guild is None or self.config.config_path is None:
                return None
            from .setup import _channel_config_block, _channel_slug

            relative = str(cwd.relative_to(project_root))
            channel_name = _channel_slug(relative)
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if channel is None:
                channel = await guild.create_text_channel(
                    channel_name,
                    topic=f"pam conversations in {cwd}",
                )
            with self.config.config_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    _channel_config_block(
                        channel.id,
                        cwd,
                        project_root=project_root,
                    )
                )
            self.config.channels[channel.id] = ChannelConfig(
                workspace=cwd,
                run_codex=True,
                instruction_prefix=project_config.instruction_prefix,
                project_record_dir=project_config.project_record_dir,
                project_root=project_root,
            )
            return channel

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
        for channel_config in self._all_channel_configs():
            discord_thread_id = self._load_channel_sessions(channel_config).get(
                codex_thread_id
            )
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
            mirror_key = (codex_thread_id, str(item_type), text)
            if _recently_mirrored(self._recent_mirrors, mirror_key, time.monotonic()):
                if item_id and imported_path is not None:
                    imported.add(item_id)
                    imported_path.parent.mkdir(parents=True, exist_ok=True)
                    _write_json(imported_path, sorted(imported))
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
            if role == "agent":
                await self._send_deliverables(
                    discord_thread,
                    text,
                    channel_config.workspace,
                    record_dir / str(discord_thread_id) if record_dir is not None else None,
                )
            if item_id and imported_path is not None:
                imported.add(item_id)
                imported_path.write_text(
                    json.dumps(sorted(imported), indent=2) + "\n", encoding="utf-8"
                )
            return

    def _load_transcriber(self) -> None:
        if self._model is None:
            self._model = WhisperModel(
                self.config.whisper_model,
                device=self.config.whisper_device,
                compute_type=self.config.whisper_compute_type,
            )

    async def _warm_transcriber(self) -> None:
        try:
            async with self._model_lock:
                await asyncio.to_thread(self._load_transcriber)
            LOG.info(
                "voice transcription ready: %s on %s",
                self.config.whisper_model,
                self.config.whisper_device,
            )
        except Exception:
            LOG.exception("failed to preload voice transcription model")

    def _transcribe(self, audio_path: Path) -> str:
        self._load_transcriber()
        assert self._model is not None
        segments, info = self._model.transcribe(
            str(audio_path), beam_size=self.config.whisper_beam_size, vad_filter=True
        )
        if info.duration > self.config.max_audio_seconds:
            raise ValueError("decoded audio exceeds configured duration limit")
        transcript = " ".join(segment.text.strip() for segment in segments).strip()
        return transcript or "[No speech detected]"

    @staticmethod
    async def _send_chunks(channel: discord.abc.Messageable, text: str) -> None:
        for offset in range(0, len(text), 1900):
            await channel.send(text[offset : offset + 1900])

    async def _send_deliverables(
        self,
        channel: discord.abc.Messageable,
        text: str,
        workspace: Path,
        conversation_dir: Path | None,
    ) -> None:
        sent_path = conversation_dir / "sent-attachments.json" if conversation_dir else None
        sent = set()
        if sent_path is not None and sent_path.exists():
            sent = set(json.loads(sent_path.read_text(encoding="utf-8")))
        guild = getattr(channel, "guild", None)
        size_limit = int(getattr(guild, "filesize_limit", 10 * 1024 * 1024))
        for path in _deliverable_paths(text, workspace):
            stat = path.stat()
            fingerprint = f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
            if fingerprint in sent:
                continue
            if stat.st_size > size_limit:
                await channel.send(
                    f"**pam** · `{path.name}` is too large for this Discord server "
                    f"({stat.st_size / 1024 / 1024:.1f} MB). Saved at `{path}`."
                )
                sent.add(fingerprint)
            else:
                await channel.send(file=discord.File(path, filename=path.name))
                sent.add(fingerprint)
            if sent_path is not None:
                sent_path.parent.mkdir(parents=True, exist_ok=True)
                _write_json(sent_path, sorted(sent))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the pam Discord agent bridge")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Private dotenv file containing DISCORD_BOT_TOKEN",
    )
    args = parser.parse_args(argv)
    # An explicit Pam instance must use its own token even when the service manager
    # inherited another Pam instance's environment.
    load_dotenv(args.env_file, override=True)
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is missing; create a local .env from .env.example")
    config = load_config(args.config)
    _acquire_instance_lock(config.instance_lock_dir)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    PamDiscord(config).run(token, log_handler=None)


if __name__ == "__main__":
    main()
