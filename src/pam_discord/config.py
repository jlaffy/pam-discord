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
        record_value = item.get("project_record_dir")
        record_dir = None
        if record_value:
            record_dir = (workspace / str(record_value)).resolve()
            if not record_dir.is_relative_to(workspace):
                raise ValueError(
                    f"project_record_dir for channel {channel_id} must stay inside its workspace"
                )
        channels[int(channel_id)] = ChannelConfig(
            workspace=workspace,
            run_codex=bool(item.get("run_codex", False)),
            instruction_prefix=str(item.get("instruction_prefix", "")).strip(),
            project_record_dir=record_dir,
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
        )
    if not channels and not guilds:
        raise ValueError("at least one Discord server or channel mapping is required")

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
            raw.get("codex_app_server_url", "ws://127.0.0.1:45832")
        ),
        instance_lock_dir=(
            Path(str(raw["instance_lock_dir"])).expanduser().resolve()
            if raw.get("instance_lock_dir")
            else None
        ),
    )
