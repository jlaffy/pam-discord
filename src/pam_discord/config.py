from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChannelConfig:
    workspace: Path
    run_codex: bool = False
    instruction_prefix: str = ""
    project_record_dir: Path | None = None
    project_root: Path | None = None
    session_state_dir: Path | None = None


@dataclass(frozen=True)
class Config:
    archive_dir: Path
    allowed_user_ids: frozenset[int]
    channels: dict[int, ChannelConfig]
    guilds: dict[int, ChannelConfig]
    max_attachment_bytes: int
    max_audio_seconds: int
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    codex_binary: str
    codex_timeout_seconds: int
    codex_app_server_url: str
    instance_lock_dir: Path | None = None
    codex_full_access: bool = True
    whisper_beam_size: int = 1
    config_path: Path | None = None
    project_roots: tuple[Path, ...] = ()
    always_on_guild_id: int | None = None
    always_on_state_dir: Path | None = None
    always_on_excluded_roots: tuple[Path, ...] = ()
    always_on_index_sessions_per_directory: int = 10
    always_on_recent_sessions_limit: int = 10
    always_on_sidebar_sessions_per_forum: int = 0
    always_on_sidebar_session_max_age_days: int = 7


def load_config(path: Path) -> Config:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    allowed = frozenset(int(value) for value in raw.get("allowed_user_ids", []))
    if not allowed:
        raise ValueError("allowed_user_ids must contain at least one Discord user ID")

    channels: dict[int, ChannelConfig] = {}
    for channel_id, item in raw.get("channels", {}).items():
        workspace = Path(item["workspace"]).expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"workspace for channel {channel_id} is not a directory: {workspace}")
        project_root = Path(item.get("project_root", workspace)).expanduser().resolve()
        if not project_root.is_dir() or (
            workspace != project_root and not workspace.is_relative_to(project_root)
        ):
            raise ValueError(
                f"project_root for channel {channel_id} must contain its workspace"
            )
        record_value = item.get("project_record_dir")
        record_dir = None
        if record_value:
            record_dir = (project_root / str(record_value)).resolve()
            if not record_dir.is_relative_to(project_root):
                raise ValueError(
                    f"project_record_dir for channel {channel_id} must stay inside its project"
                )
        channels[int(channel_id)] = ChannelConfig(
            workspace=workspace,
            run_codex=bool(item.get("run_codex", False)),
            instruction_prefix=str(item.get("instruction_prefix", "")).strip(),
            project_record_dir=record_dir,
            project_root=project_root,
        )
    guilds: dict[int, ChannelConfig] = {}
    for guild_id, item in raw.get("guilds", {}).items():
        workspace = Path(item["workspace"]).expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"workspace for server {guild_id} is not a directory: {workspace}")
        record_value = item.get("project_record_dir")
        record_dir = (workspace / str(record_value)).resolve() if record_value else None
        if record_dir is not None and not record_dir.is_relative_to(workspace):
            raise ValueError(
                f"project_record_dir for server {guild_id} must stay inside its workspace"
            )
        guilds[int(guild_id)] = ChannelConfig(
            workspace=workspace,
            run_codex=bool(item.get("run_codex", False)),
            instruction_prefix=str(item.get("instruction_prefix", "")).strip(),
            project_record_dir=record_dir,
            project_root=workspace,
        )
    always_on = raw.get("always_on")
    always_on_guild_id = None
    always_on_state_dir = None
    always_on_roots: tuple[Path, ...] = ()
    always_on_excluded_roots: tuple[Path, ...] = ()
    always_on_index_sessions_per_directory = 10
    always_on_recent_sessions_limit = 10
    always_on_sidebar_sessions_per_forum = 0
    always_on_sidebar_session_max_age_days = 7
    if isinstance(always_on, dict) and always_on.get("guild_id"):
        always_on_guild_id = int(always_on["guild_id"])
        always_on_state_dir = Path(
            str(always_on.get("state_dir", "./always-on-state"))
        ).expanduser().resolve()
        roots = always_on.get("approved_roots", [])
        always_on_roots = tuple(
            Path(str(value)).expanduser().resolve() for value in roots
        )
        if not always_on_roots:
            raise ValueError("always_on approved_roots must contain at least one directory")
        for root in always_on_roots:
            if not root.is_dir():
                raise ValueError(f"always_on approved root is not a directory: {root}")
        always_on_excluded_roots = tuple(
            Path(str(value)).expanduser().resolve()
            for value in always_on.get("excluded_roots", [])
        )
        always_on_index_sessions_per_directory = int(
            always_on.get("index_sessions_per_directory", 10)
        )
        if not 1 <= always_on_index_sessions_per_directory <= 50:
            raise ValueError("always_on index_sessions_per_directory must be 1-50")
        always_on_recent_sessions_limit = int(
            always_on.get("recent_sessions_limit", 10)
        )
        if not 1 <= always_on_recent_sessions_limit <= 25:
            raise ValueError("always_on recent_sessions_limit must be 1-25")
        always_on_sidebar_sessions_per_forum = int(
            always_on.get("sidebar_sessions_per_forum", 0)
        )
        if not 0 <= always_on_sidebar_sessions_per_forum <= 10:
            raise ValueError("always_on sidebar_sessions_per_forum must be 0-10")
        always_on_sidebar_session_max_age_days = int(
            always_on.get("sidebar_session_max_age_days", 7)
        )
        if not 1 <= always_on_sidebar_session_max_age_days <= 90:
            raise ValueError("always_on sidebar_session_max_age_days must be 1-90")
    if not channels and not guilds and always_on_guild_id is None:
        raise ValueError("at least one Discord server or channel mapping is required")
    project_roots: tuple[Path, ...] = ()
    hub = raw.get("hub")
    if isinstance(hub, dict) and hub.get("projects_root"):
        projects_root = Path(str(hub["projects_root"])).expanduser().resolve()
        if not projects_root.is_dir():
            raise ValueError(f"hub projects_root is not a directory: {projects_root}")
        project_roots = (projects_root,)

    max_mb = int(raw.get("max_attachment_mb", 25))
    max_seconds = int(raw.get("max_audio_seconds", 1800))
    if not 1 <= max_mb <= 100 or not 1 <= max_seconds <= 7200:
        raise ValueError("limits must be 1-100 MB and 1-7200 seconds")

    return Config(
        archive_dir=Path(raw.get("archive_dir", "./archive")).expanduser().resolve(),
        allowed_user_ids=allowed,
        channels=channels,
        guilds=guilds,
        max_attachment_bytes=max_mb * 1024 * 1024,
        max_audio_seconds=max_seconds,
        whisper_model=str(raw.get("whisper_model", "small.en")),
        whisper_device=str(raw.get("whisper_device", "cpu")),
        whisper_compute_type=str(raw.get("whisper_compute_type", "int8")),
        codex_binary=str(raw.get("codex_binary", "codex")),
        codex_timeout_seconds=int(raw.get("codex_timeout_seconds", 1800)),
        codex_app_server_url=str(
            raw.get("codex_app_server_url", "stdio://")
        ),
        instance_lock_dir=(
            Path(str(raw["instance_lock_dir"])).expanduser().resolve()
            if raw.get("instance_lock_dir")
            else None
        ),
        codex_full_access=bool(raw.get("codex_full_access", True)),
        whisper_beam_size=int(raw.get("whisper_beam_size", 1)),
        config_path=path.expanduser().resolve(),
        project_roots=tuple(dict.fromkeys((*project_roots, *always_on_roots))),
        always_on_guild_id=always_on_guild_id,
        always_on_state_dir=always_on_state_dir,
        always_on_excluded_roots=always_on_excluded_roots,
        always_on_index_sessions_per_directory=always_on_index_sessions_per_directory,
        always_on_recent_sessions_limit=always_on_recent_sessions_limit,
        always_on_sidebar_sessions_per_forum=always_on_sidebar_sessions_per_forum,
        always_on_sidebar_session_max_age_days=always_on_sidebar_session_max_age_days,
    )
