import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MANUFACTURING_CONFIG_FILE", str(ROOT / "config.example.json"))

import configure_tools


class ToolConfigurationTests(unittest.TestCase):
    @patch("configure_tools.subprocess.run")
    def test_project_operations_pin_the_configured_endpoint(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="{}", stderr="")
        configure_tools.azd("ai", "toolbox", "list")
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--project-endpoint", configure_tools.ENDPOINT])

    @patch("configure_tools.subprocess.run")
    def test_environment_commands_do_not_receive_project_flags(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        configure_tools.azd("env", "set", "TOOLBOX_NAME", "manufacturing-tools")
        self.assertNotIn("--project-endpoint", run.call_args.args[0])

    @patch("configure_tools.subprocess.run")
    def test_cli_errors_include_standard_output_diagnostics(self, run):
        run.return_value = SimpleNamespace(returncode=1, stdout="ERROR: invalid endpoint", stderr="")
        with self.assertRaisesRegex(RuntimeError, "invalid endpoint"):
            configure_tools.azd("ai", "toolbox", "list")


if __name__ == "__main__":
    unittest.main()
