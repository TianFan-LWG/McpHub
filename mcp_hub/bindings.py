from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import McpDefinition
from .errors import HubError

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_:]+)\}")


@dataclass(frozen=True)
class ResolvedBinding:
    name: str
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str | None
    instance_key: str
    project_root: str | None
    db_path: str | None
    strategy: str


def normalize_path(value: str) -> str:
    path = Path(value).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    text = str(path).replace("\\", "/")
    return text.lower() if os.name == "nt" else text


def build_context(project: str | None, db_file: str | None) -> dict[str, str]:
    ctx: dict[str, str] = {"HOME": str(Path.home())}
    if project:
        root = Path(project).expanduser().resolve()
        ctx["PROJECT_ROOT"] = str(root)
        ctx["PROJECT_NAME"] = root.name
        ctx["PROJECT_ID"] = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
        ctx["CWD"] = str(root)
    if db_file:
        candidate = Path(db_file).expanduser()
        if not candidate.is_absolute():
            if not project:
                raise HubError("PROJECT_REQUIRED", f"db_file '{db_file}' is relative and needs a project")
            candidate = Path(project).expanduser().resolve() / candidate
        ctx["DB_PATH"] = str(candidate)
    return ctx


def render(template: str, ctx: dict[str, str], *, name: str) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key.startswith("ENV:"):
            env_key = key[4:]
            if env_key not in os.environ:
                raise HubError(
                    "VAR_MISSING",
                    f"environment variable '{env_key}' is not set (mcp '{name}')",
                    mcp=name,
                    variable=key,
                )
            return os.environ[env_key]
        if key in ctx:
            return ctx[key]
        code = "PROJECT_REQUIRED" if key.startswith("PROJECT") or key in {"CWD", "DB_PATH"} else "VAR_MISSING"
        raise HubError(
            code,
            f"cannot resolve '${{{key}}}' for mcp '{name}'",
            mcp=name,
            variable=key,
        )

    return _PLACEHOLDER.sub(replace, template)


def resolve(defn: McpDefinition, project: str | None) -> ResolvedBinding:
    bindings = defn.bindings
    ctx = build_context(project, bindings.db_file)

    for variable in bindings.required:
        key = variable.strip().strip("${}")
        if key not in ctx:
            code = "PROJECT_REQUIRED" if key.startswith("PROJECT") or key in {"CWD", "DB_PATH"} else "VAR_MISSING"
            raise HubError(
                code,
                f"mcp '{defn.name}' requires '{key}'",
                mcp=defn.name,
                variable=key,
            )

    if bindings.strategy in {"per_project", "per_file"} and not project:
        raise HubError(
            "PROJECT_REQUIRED",
            f"mcp '{defn.name}' uses strategy '{bindings.strategy}' and requires a project",
            mcp=defn.name,
        )

    args = [render(item, ctx, name=defn.name) for item in defn.args]
    env = {key: render(value, ctx, name=defn.name) for key, value in defn.env.items()}
    cwd = render(defn.cwd, ctx, name=defn.name) if defn.cwd else None

    if bindings.strategy == "global":
        instance_key = f"{defn.name}:__global__"
    elif bindings.strategy == "per_file":
        target = ctx.get("DB_PATH") or ctx.get("PROJECT_ROOT")
        if not target:
            raise HubError(
                "PROJECT_REQUIRED",
                f"mcp '{defn.name}' uses strategy 'per_file' but no file target was resolved",
                mcp=defn.name,
            )
        instance_key = f"{defn.name}:{normalize_path(target)}"
    else:
        instance_key = f"{defn.name}:{normalize_path(ctx['PROJECT_ROOT'])}"

    return ResolvedBinding(
        name=defn.name,
        command=defn.command,
        args=args,
        env=env,
        cwd=cwd,
        instance_key=instance_key,
        project_root=ctx.get("PROJECT_ROOT"),
        db_path=ctx.get("DB_PATH"),
        strategy=bindings.strategy,
    )
