import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "release_state", Path(__file__).resolve().parents[1] / "one_stop/release_state.py")
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


class ReleaseStateTest(unittest.TestCase):
    def test_failed_update_preserves_current_and_reports_partial_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1, "release": "2.0.0-1", "build_id": "first",
                "target": "orin-humble", "modules": {},
            }))
            state.transition(root, "begin", manifest, "WA1")
            self.assertFalse((root / "current.json").exists())
            state.transition(root, "complete")
            original = state.read(root / "current.json")
            self.assertEqual(original["release"], "2.0.0-1")
            with contextlib.redirect_stdout(io.StringIO()) as baseline_output:
                self.assertEqual(state.show(root), 0)
            self.assertIn("2.0.0-1", baseline_output.getvalue())
            state.transition(root, "begin", manifest, "WA2")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(state.show(root), 2)
            self.assertIn("partially updated", output.getvalue())
            state.transition(root, "fail")
            self.assertEqual(state.read(root / "current.json"), original)
            self.assertEqual(state.read(root / "status.json")["installation_status"], "failed")
            state.transition(root, "begin", manifest, "WA2")
            state.transition(root, "complete")
            self.assertEqual(state.read(root / "previous.json"), original)
            self.assertEqual(state.read(root / "current.json")["robot_type"], "WA2")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(state.show(root), 0)
