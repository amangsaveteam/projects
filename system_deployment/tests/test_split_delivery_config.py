import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("builder", Path(__file__).resolve().parents[1] / "one_stop/build_one_stop_package.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class SplitConfigTest(unittest.TestCase):
    def test_alias_runtime_and_operations_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = root / "module.env"
            env.write_text("export EXAMPLE_VALUE=from_file\n")
            packages = root / "package-urls.json"
            packages.write_text(json.dumps({"targets": {"orin-humble": {"runs": [{
                "name": "example-package", "runtime": {
                    "source_files": [str(env)], "environment": {"EXAMPLE_VALUE": "from_developer"}
                }}]}}}))
            operations = {"schema_version": 1, "targets": {"orin-humble": {"supervisor_modules": [{
                "id": "example", "package": "example-package", "mode": "managed", "port": 19006,
                "working_directory": temporary, "command": "/usr/bin/printenv EXAMPLE_VALUE",
                "prelude": ["export EXAMPLE_VALUE=from_operations"]
            }]}}}
            (root / "supervisor.json").write_text(json.dumps(operations))
            config = builder.load_delivery(packages)
            module = config["targets"]["orin-humble"]["supervisor_modules"][0]
            result = subprocess.run(["bash", "-c", builder.supervisor_launch_script(module)],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), "from_operations")
            operations["targets"]["orin-humble"]["supervisor_modules"][0]["mode"] = "external"
            (root / "supervisor.json").write_text(json.dumps(operations))
            with self.assertRaisesRegex(builder.BuildError, "external runtime"):
                builder.load_delivery(packages)

    def test_install_environment_is_literal_and_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "installer.sh").write_text('printf "%s" "$EXAMPLE_VALUE"\n')
            value = "space $(false) ' literal"
            env = builder.resolve_environment({"EXAMPLE_VALUE": value}, "test")
            command = "env " + " ".join(env) + " " + builder.render_run_command("installer.sh", [])
            result = subprocess.run(["bash", "-c", command], env={"root": str(root), "PATH": "/usr/bin:/bin"},
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, value)

    def test_current_split_config_loads_without_mutating_packages(self):
        path = Path(__file__).resolve().parents[1] / "one_stop/package-urls.json"
        self.assertNotIn("supervisor_modules", builder.load(path)["targets"]["orin-humble"])
        joined = builder.load_delivery(path)["targets"]["orin-humble"]
        self.assertIn("supervisor_modules", joined)
        audio = next(p for p in joined["runs"] if p["name"] == "audio")
        self.assertEqual(audio["start_policy"], "supervisor")
