import tempfile
import unittest
from pathlib import Path

from mcp_hub.bindings import normalize_path, resolve
from mcp_hub.config import Bindings, HubConfig, McpDefinition, merge_overlay
from mcp_hub.errors import HubError


def sqlite_def(**kwargs) -> McpDefinition:
    bindings = kwargs.pop(
        "bindings",
        Bindings(strategy="per_project", required=["PROJECT_ROOT", "DB_PATH"], db_file="data/app.db"),
    )
    return McpDefinition(
        name="sqlite",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sqlite", "--db-path", "${DB_PATH}"],
        bindings=bindings,
        **kwargs,
    )


class BindingsTest(unittest.TestCase):
    def test_per_project_renders_db_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolved = resolve(sqlite_def(), tmp)
            expected = str(Path(tmp).resolve() / "data" / "app.db")
            self.assertEqual(resolved.args[-1], expected)
            self.assertEqual(resolved.instance_key, f"sqlite:{normalize_path(tmp)}")
            self.assertEqual(resolved.db_path, expected)

    def test_per_project_requires_project(self) -> None:
        with self.assertRaises(HubError) as ctx:
            resolve(sqlite_def(), None)
        self.assertEqual(ctx.exception.code, "PROJECT_REQUIRED")

    def test_project_isolation_keys_differ(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertNotEqual(resolve(sqlite_def(), a).instance_key, resolve(sqlite_def(), b).instance_key)

    def test_global_strategy_ignores_project(self) -> None:
        definition = McpDefinition(name="time", command="x", bindings=Bindings(strategy="global"))
        first = resolve(definition, None)
        second = resolve(definition, "C:/some/project")
        self.assertEqual(first.instance_key, "time:__global__")
        self.assertEqual(first.instance_key, second.instance_key)

    def test_absolute_db_file_used_as_is(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            target = Path(other) / "custom.db"
            definition = sqlite_def(
                bindings=Bindings(strategy="per_file", required=["DB_PATH"], db_file=str(target))
            )
            resolved = resolve(definition, tmp)
            self.assertEqual(resolved.db_path, str(target))
            self.assertEqual(resolved.instance_key, f"sqlite:{normalize_path(str(target))}")

    def test_env_template_renders(self) -> None:
        definition = McpDefinition(
            name="aoci",
            command="aoci-mcp",
            env={"AOCI_PROJECT": "${PROJECT_ROOT}"},
            bindings=Bindings(strategy="per_project"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            resolved = resolve(definition, tmp)
            self.assertEqual(resolved.env["AOCI_PROJECT"], str(Path(tmp).resolve()))

    def test_missing_placeholder_raises(self) -> None:
        definition = McpDefinition(
            name="bad",
            command="x",
            args=["${DB_PATH}"],
            bindings=Bindings(strategy="per_project", required=["PROJECT_ROOT"]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(HubError) as ctx:
                resolve(definition, tmp)
        self.assertEqual(ctx.exception.code, "PROJECT_REQUIRED")


class OverlayTest(unittest.TestCase):
    def test_bindings_sugar_overrides_without_mutating_base(self) -> None:
        cfg = HubConfig(mcps={"sqlite": sqlite_def()})
        merged = merge_overlay(cfg, {"bindings": {"sqlite": {"db_file": "local.db"}}})
        self.assertEqual(merged.mcps["sqlite"].bindings.db_file, "local.db")
        self.assertEqual(cfg.mcps["sqlite"].bindings.db_file, "data/app.db")

    def test_mcps_partial_patch_merges_nested(self) -> None:
        cfg = HubConfig(mcps={"sqlite": sqlite_def()})
        merged = merge_overlay(cfg, {"mcps": {"sqlite": {"bindings": {"db_file": "x.db"}}}})
        self.assertEqual(merged.mcps["sqlite"].bindings.db_file, "x.db")
        self.assertEqual(merged.mcps["sqlite"].command, "npx")


if __name__ == "__main__":
    unittest.main()
