"""Configuration helpers for local-only VoiceTrack settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read_env_file(path: Path) -> None:
    """Load simple KEY=value pairs without requiring another dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    """Runtime settings with beginner-friendly defaults."""

    ollama_host: str
    ollama_model: str
    ollama_fallback_model: str
    ollama_timeout_seconds: int
    db_path: Path
    vosk_model_path: Path


def load_settings() -> Settings:
    """Read settings from .env, environment variables, then defaults."""
    _read_env_file(Path(".env"))
    home = Path.home()
    default_db = home / "VoiceTrack" / "data.db"
    db_path = os.path.expandvars(os.getenv("VOICETRACK_DB_PATH", str(default_db)))
    vosk_model_path = os.path.expandvars(os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15"))
    return Settings(
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "mistral:7b-instruct-q4_K_M"),
        ollama_fallback_model=os.getenv("OLLAMA_FALLBACK_MODEL", "llama3.2:3b"),
        ollama_timeout_seconds=int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "20")),
        db_path=Path(db_path).expanduser(),
        vosk_model_path=Path(vosk_model_path).expanduser(),
    )
