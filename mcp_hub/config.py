from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Strategy = Literal["global", "per_project", "per_file"]


class Bindings(BaseModel):
    strategy: Strategy = "global"
    required: list[str] = Field(default_factory=list)
    db_file: str | None = None


class McpDefinition(BaseModel):
    name: str = ""
    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True
    description: str | None = None
    bindings: Bindings = Field(default_factory=Bindings)


class Logging(BaseModel):
    level: str = "INFO"
    dir: str | None = None  # None -> ~/.mcp-hub/logs（可用 MCPHUB_LOG_DIR 覆盖）
    stderr: bool = False
    backup_days: int = 14


class Defaults(BaseModel):
    idle_timeout_minutes: int = 30
    max_instances: int = 16
    call_timeout_seconds: int = 60
    start_timeout_seconds: int = 90
    default_project: str | None = None
    logging: Logging = Field(default_factory=Logging)


class HubConfig(BaseModel):
    version: int = 1
    defaults: Defaults = Field(default_factory=Defaults)
    mcps: dict[str, McpDefinition] = Field(default_factory=dict)


def default_config_path() -> Path:
    env = os.environ.get("MCPHUB_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".mcp-hub" / "hub.json"


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"config must be a JSON object: {path}")
    return data


def load_config(path: Path | str | None = None) -> HubConfig:
    target = Path(path).expanduser() if path else default_config_path()
    if not target.exists():
        return HubConfig()
    cfg = HubConfig.model_validate(_read_json(target))
    for name, defn in cfg.mcps.items():
        if not defn.name:
            defn.name = name
    return cfg


def save_config(cfg: HubConfig, path: Path | str | None = None) -> Path:
    target = Path(path).expanduser() if path else default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="json")
    for name, defn in data["mcps"].items():
        defn["name"] = name
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_project_overlay(project_root: str | Path) -> dict[str, Any]:
    path = Path(project_root).expanduser() / ".mcp-hub.json"
    if not path.exists():
        return {}
    return _read_json(path)


def _deep_update(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def merge_overlay(cfg: HubConfig, overlay: dict[str, Any]) -> HubConfig:
    if not overlay:
        return cfg
    merged = cfg.model_copy(deep=True)

    for name, patch in (overlay.get("bindings") or {}).items():
        defn = merged.mcps.get(name)
        if defn is None:
            continue
        for key, value in patch.items():
            if key in Bindings.model_fields:
                setattr(defn.bindings, key, value)

    for name, patch in (overlay.get("mcps") or {}).items():
        if name in merged.mcps:
            base = merged.mcps[name].model_dump()
            _deep_update(base, patch)
            merged.mcps[name] = McpDefinition.model_validate(base)
        else:
            data = dict(patch)
            data.setdefault("name", name)
            merged.mcps[name] = McpDefinition.model_validate(data)

    if isinstance(overlay.get("defaults"), dict):
        base = merged.defaults.model_dump()
        _deep_update(base, overlay["defaults"])
        merged.defaults = Defaults.model_validate(base)

    return merged
