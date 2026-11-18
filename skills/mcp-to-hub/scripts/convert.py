#!/usr/bin/env python3
"""Convert plain MCP server configs into McpHub child MCP entries.

Accepts:
  - {"mcpServers": {"<name>": {command, args, env, ...}}}
  - {"<name>": {command, args, env, ...}}
  - {"command": "...", "args": [...], ...}   (single server, needs --name)

Detects absolute paths in args/env/cwd, replaces them with ${PROJECT_ROOT} or
${DB_PATH}, and infers a binding strategy.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DB_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db3")
WINDOWS_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
POSIX_ABS = re.compile(r"^/(?!/)")


def is_absolute_path(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("${") or "://" in value:
        return False
    return bool(WINDOWS_ABS.match(value) or POSIX_ABS.match(value))


def classify_path(value: str) -> dict | None:
    if not is_absolute_path(value):
        return None
    lowered = value.lower().rstrip("/\\")
    if lowered.endswith(DB_SUFFIXES):
        return {"kind": "db", "template": "${DB_PATH}", "original": value}
    return {"kind": "dir", "template": "${PROJECT_ROOT}", "original": value}


def relative_to_project(path: str, project: str) -> str | None:
    try:
        return Path(path).resolve().relative_to(Path(project).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def convert_server(name: str, server: dict, project: str | None) -> dict:
    detected: list[dict] = []
    warnings: list[str] = []

    if not isinstance(server, dict):
        return {"entry": None, "detected": [], "warnings": [f"'{name}' is not an object"]}

    new_args = []
    for index, item in enumerate(server.get("args") or []):
        info = classify_path(item) if isinstance(item, str) else None
        if info:
            new_args.append(info["template"])
            detected.append({"where": f"args[{index}]", **info})
        else:
            new_args.append(item)

    new_env = {}
    for key, item in (server.get("env") or {}).items():
        info = classify_path(item) if isinstance(item, str) else None
        if info:
            new_env[key] = info["template"]
            detected.append({"where": f"env.{key}", **info})
        else:
            new_env[key] = item

    new_cwd = None
    if server.get("cwd"):
        cwd_info = classify_path(server["cwd"])
        if cwd_info:
            new_cwd = cwd_info["template"]
            detected.append({"where": "cwd", **cwd_info})
        else:
            new_cwd = server["cwd"]

    kinds = {item["kind"] for item in detected}
    if "db" in kinds:
        db_item = next(item for item in detected if item["kind"] == "db")
        relative = relative_to_project(db_item["original"], project) if project else None
        if relative:
            bindings = {
                "strategy": "per_project",
                "required": ["PROJECT_ROOT", "DB_PATH"],
                "db_file": relative,
            }
        else:
            bindings = {"strategy": "per_file", "required": ["DB_PATH"]}
            warnings.append(
                "数据库路径不在 --project 范围内，已按 per_file 处理；"
                "若它属于项目，应改为 per_project 并在 bindings.db_file 填相对路径"
            )
        if "dir" in kinds:
            warnings.append("同一 MCP 同时出现项目目录与数据库路径，请确认二者是否都需要绑定")
    elif "dir" in kinds:
        bindings = {"strategy": "per_project", "required": ["PROJECT_ROOT"]}
    else:
        bindings = {"strategy": "global"}

    command = server.get("command") or ""
    if not command:
        warnings.append("缺少 command，转换结果无法启动")

    entry = {
        "name": name,
        "type": "stdio",
        "command": command,
        "args": new_args,
        "env": new_env,
        "enabled": server.get("enabled", True),
        "bindings": bindings,
    }
    if new_cwd:
        entry["cwd"] = new_cwd
    if server.get("description"):
        entry["description"] = server["description"]

    return {"entry": entry, "detected": detected, "warnings": warnings}


def extract_servers(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    if isinstance(data.get("mcpServers"), dict):
        return data["mcpServers"]
    if "command" in data:
        return {"__single__": data}
    if data and all(isinstance(value, dict) for value in data.values()):
        return data
    raise ValueError("cannot find mcpServers or a server definition in input")


def merge_into_hub(hub_path: Path, results: dict, force: bool) -> list[str]:
    if hub_path.exists():
        hub = json.loads(hub_path.read_text(encoding="utf-8"))
    else:
        hub = {"version": 1, "defaults": {}, "mcps": {}}
    hub.setdefault("mcps", {})
    conflicts = [name for name in results if name in hub["mcps"]]
    if conflicts and not force:
        raise ValueError(f"already exists in hub config: {', '.join(conflicts)} (use --force)")
    for name, result in results.items():
        if result["entry"] is not None:
            hub["mcps"][name] = result["entry"]
    hub_path.parent.mkdir(parents=True, exist_ok=True)
    hub_path.write_text(json.dumps(hub, ensure_ascii=False, indent=2), encoding="utf-8")
    return conflicts


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert plain MCP config to McpHub child MCP entries")
    parser.add_argument("input", nargs="?", default="-", help="JSON file path, or - for stdin")
    parser.add_argument("--name", help="only convert this server")
    parser.add_argument("--project", help="project root, used to compute relative db_file")
    parser.add_argument("--write", help="merge converted entries into this hub.json")
    parser.add_argument("--force", action="store_true", help="overwrite existing entries when writing")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON only")
    args = parser.parse_args()

    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read config: {exc}", file=sys.stderr)
        return 2

    try:
        servers = extract_servers(data)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.name:
        if args.name not in servers:
            print(f"error: server '{args.name}' not found; available: {', '.join(servers)}", file=sys.stderr)
            return 2
        if args.name == "__single__":
            servers = {args.name: servers[args.name]}
        else:
            servers = {args.name: servers[args.name]}

    results = {}
    for name, server in servers.items():
        display = args.name or name
        results[display] = convert_server(display, server, args.project)

    if args.write:
        try:
            merge_into_hub(Path(args.write), results, args.force)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    payload = {
        "ok": True,
        "project": args.project,
        "written_to": args.write,
        "servers": results,
        "warnings": [f"{name}: {w}" for name, r in results.items() for w in r["warnings"]],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Converted {len(results)} server(s)")
    for name, result in results.items():
        entry = result["entry"]
        if entry is None:
            print(f"\n[{name}] skipped")
            continue
        print(f"\n[{name}] strategy={entry['bindings']['strategy']}")
        for item in result["detected"]:
            print(f"  {item['where']}: {item['original']} -> {item['template']}")
        for warning in result["warnings"]:
            print(f"  ! {warning}")
        print("  " + json.dumps(entry, ensure_ascii=False))
    if args.write:
        print(f"\nMerged into {args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
