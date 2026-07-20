from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChannelConfig:
    workspace: Path
    run_codex: bool = False


@dataclass(frozen=True)
class Config:
    archive_dir: Path
    allowed_user_ids: frozenset[int]
    channels: dict[int, ChannelConfig]
    max_attachment_bytes: int
    max_audio_seconds: int
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    codex_binary: str
    codex_timeout_seconds: int


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
        channels[int(channel_id)] = ChannelConfig(workspace, bool(item.get("run_codex", False)))
    if not channels:
        raise ValueError("at least one [channels.\"ID\"] mapping is required")

    max_mb = int(raw.get("max_attachment_mb", 25))
    max_seconds = int(raw.get("max_audio_seconds", 1800))
    if not 1 <= max_mb <= 100 or not 1 <= max_seconds <= 7200:
        raise ValueError("limits must be 1-100 MB and 1-7200 seconds")

    return Config(
        archive_dir=Path(raw.get("archive_dir", "./archive")).expanduser().resolve(),
        allowed_user_ids=allowed,
        channels=channels,
        max_attachment_bytes=max_mb * 1024 * 1024,
        max_audio_seconds=max_seconds,
        whisper_model=str(raw.get("whisper_model", "small.en")),
        whisper_device=str(raw.get("whisper_device", "cpu")),
        whisper_compute_type=str(raw.get("whisper_compute_type", "int8")),
        codex_binary=str(raw.get("codex_binary", "codex")),
        codex_timeout_seconds=int(raw.get("codex_timeout_seconds", 1800)),
    )

