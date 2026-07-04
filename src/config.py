"""Configuration loading and per-model resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_CONFIG_NAMES = ("config.yaml", "config.example.yaml")


@dataclass
class ModelPrice:
    """USD price per 1M tokens for each token category."""

    input: float = 0.0
    output: float = 0.0
    cache_write: float = 0.0
    cache_read: float = 0.0

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["ModelPrice"]:
        if not data:
            return None
        return cls(
            input=float(data.get("input", 0.0)),
            output=float(data.get("output", 0.0)),
            cache_write=float(data.get("cache_write", 0.0)),
            cache_read=float(data.get("cache_read", 0.0)),
        )


@dataclass
class ModelConfig:
    match: str
    display_name: str
    weekly_token_limit: int
    color: tuple[int, int, int]
    weekly_cost_limit: Optional[float] = None
    price: Optional[ModelPrice] = None


@dataclass
class Config:
    refresh_seconds: int = 30
    brightness: int = 60
    session_window_hours: float = 5.0
    weekly_window_days: int = 7
    claude_projects_dir: Path = field(
        default_factory=lambda: Path("~/.claude/projects").expanduser()
    )
    models: list[ModelConfig] = field(default_factory=list)
    default_model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            match="",
            display_name="?",
            weekly_token_limit=100_000_000,
            color=(150, 150, 150),
        )
    )
    limit_metric: str = "tokens"
    session_token_limit: int = 40_000_000

    def resolve_model(self, model_id: str) -> ModelConfig:
        """Return the first ModelConfig whose match is a substring of model_id."""
        lowered = (model_id or "").lower()
        for m in self.models:
            if m.match and m.match.lower() in lowered:
                return m
        # Fall back to the default but keep a readable display name.
        display = _prettify_model_id(model_id)
        return ModelConfig(
            match="",
            display_name=display,
            weekly_token_limit=self.default_model.weekly_token_limit,
            color=self.default_model.color,
            weekly_cost_limit=self.default_model.weekly_cost_limit,
        )


def _prettify_model_id(model_id: str) -> str:
    if not model_id:
        return "?"
    parts = model_id.replace("claude-", "").split("-")
    return parts[0].capitalize()[:6] if parts else model_id[:6]


def _to_color(value, fallback=(150, 150, 150)) -> tuple[int, int, int]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return (int(value[0]), int(value[1]), int(value[2]))
    return fallback


def _parse_model(data: dict, default_color) -> ModelConfig:
    return ModelConfig(
        match=str(data.get("match", "")),
        display_name=str(data.get("display_name", data.get("match", "?"))),
        weekly_token_limit=int(data.get("weekly_token_limit", 100_000_000)),
        color=_to_color(data.get("color"), default_color),
        weekly_cost_limit=(
            float(data["weekly_cost_limit"]) if data.get("weekly_cost_limit") is not None else None
        ),
        price=ModelPrice.from_dict(data.get("price")),
    )


def find_config_path(explicit: Optional[str] = None) -> Path:
    """Locate a config file, preferring config.yaml over the example."""
    if explicit:
        return Path(explicit).expanduser()
    root = Path(__file__).resolve().parent.parent
    for name in DEFAULT_CONFIG_NAMES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return root / DEFAULT_CONFIG_NAMES[0]


def load_config(path: Optional[str] = None) -> Config:
    config_path = find_config_path(path)
    data: dict = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    default_model_data = data.get("default_model", {}) or {}
    default_color = _to_color(default_model_data.get("color"))
    default_model = ModelConfig(
        match="",
        display_name="?",
        weekly_token_limit=int(default_model_data.get("weekly_token_limit", 100_000_000)),
        color=default_color,
        weekly_cost_limit=(
            float(default_model_data["weekly_cost_limit"])
            if default_model_data.get("weekly_cost_limit") is not None
            else None
        ),
    )

    models = [_parse_model(m, default_color) for m in (data.get("models") or [])]

    projects_dir = data.get("claude_projects_dir", "~/.claude/projects")

    cfg = Config(
        refresh_seconds=int(data.get("refresh_seconds", 30)),
        brightness=int(data.get("brightness", 60)),
        session_window_hours=float(data.get("session_window_hours", 5.0)),
        weekly_window_days=int(data.get("weekly_window_days", 7)),
        claude_projects_dir=Path(os.path.expanduser(str(projects_dir))),
        models=models,
        default_model=default_model,
        limit_metric=str(data.get("limit_metric", "tokens")).lower(),
        session_token_limit=int(data.get("session_token_limit", 40_000_000)),
    )
    return cfg
