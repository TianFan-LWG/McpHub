import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_hub.hub import Hub

ROOT = Path(__file__).resolve().parents[1]

HUB_CONFIG = {
    "version": 1,
    "defaults": {
        "idle_timeout_minutes": 30,
        "max_instances": 16,
        "call_timeout_seconds": 90,
        "start_timeout_seconds": 180,
        "default_project": None,
    },
    "mcps": {
        "sqlite": {
            "name": "sqlite",
            "type": "stdio",
            "command": "uvx",
            "args": ["--with", "mcp<2", "mcp-server-sqlite", "--db-path", "${DB_PATH}"],
            "enabled": True,
            "description": "Query project-local SQLite databases.",
            "bindings": {
                "strategy": "per_project",
                "required": ["PROJECT_ROOT", "DB_PATH"],
                "db_file": "data/app.db",
            },
        }
    },
}


def make_project(root: Path, name: str, rows: list[str]) -> Path:
    project = root / name
    (project / "data").mkdir(parents=True)
    conn = sqlite3.connect(project / "data" / "app.db")
    conn.execute("create table notes (body text)")
    conn.executemany("insert into notes (body) values (?)", [(row,) for row in rows])
    conn.commit()
    conn.close()
    return project


class SqliteE2ETest(unittest.IsolatedAsyncioTestCase):
    async def test_two_projects_hit_different_databases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            proj_a = make_project(base, "proj-a", ["alpha"])
            proj_b = make_project(base, "proj-b", ["beta-1", "beta-2"])
            config_path = base / "hub.json"
            config_path.write_text(json.dumps(HUB_CONFIG), encoding="utf-8")

            hub = Hub(config_path=config_path)
            try:
                res_a = await hub.call(
                    "sqlite", "read_query", {"query": "select count(*) as n from notes"}, project=str(proj_a)
                )
                res_b = await hub.call(
                    "sqlite", "read_query", {"query": "select count(*) as n from notes"}, project=str(proj_b)
                )
            finally:
                await hub.close()

            text_a = "".join(block.get("text", "") for block in res_a["content"])
            text_b = "".join(block.get("text", "") for block in res_b["content"])
            self.assertIn("1", text_a)
            self.assertIn("2", text_b)
            self.assertNotEqual(res_a["instance"], res_b["instance"])
            self.assertIn("proj-a", res_a["instance"].replace("\\", "/"))


class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_hub_speaks_mcp_over_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "hub.json"
            config_path.write_text(json.dumps(HUB_CONFIG), encoding="utf-8")
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mcp_hub"],
                env={**os.environ, "MCPHUB_CONFIG": str(config_path)},
                cwd=str(ROOT),
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    names = {tool.name for tool in listed.tools}
                    self.assertTrue(
                        {"mcp_list", "mcp_get", "mcp_install", "mcp_update", "mcp_remove", "mcp_restart",
                         "mcp_set_context", "mcp_tools", "mcp_call"}.issubset(names)
                    )
                    result = await session.call_tool("mcp_list", {})
                    text = "".join(getattr(block, "text", "") for block in result.content)
                    payload = json.loads(text)
                    self.assertTrue(payload["ok"])
                    self.assertEqual(payload["mcps"][0]["name"], "sqlite")
                    self.assertEqual(payload["mcps"][0]["description"], "Query project-local SQLite databases.")


if __name__ == "__main__":
    unittest.main()
