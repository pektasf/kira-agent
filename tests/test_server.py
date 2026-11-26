import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

import server


class ServerHelpersTest(unittest.TestCase):
    def test_frequency_defaults_and_clamps(self):
        original = server.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as directory:
                server.SETTINGS_PATH = Path(directory) / "settings.json"
                self.assertEqual(server.get_frequency(), 60)
                server.write_json(server.SETTINGS_PATH, {"frequency_minutes": 5})
                self.assertEqual(server.get_frequency(), 15)
                server.write_json(server.SETTINGS_PATH, {"frequency_minutes": 2000})
                self.assertEqual(server.get_frequency(), 1440)
        finally:
            server.SETTINGS_PATH = original

    def test_json_write_is_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "value.json"
            payload = {"district": "Kağıthane", "enabled": True}
            server.write_json(path, payload)
            self.assertEqual(server.read_json(path, {}), payload)

    def test_basic_auth_requires_configured_credentials(self):
        old_user = os.environ.pop("KIRA_ADMIN_USER", None)
        old_password = os.environ.pop("KIRA_ADMIN_PASSWORD", None)
        try:
            self.assertFalse(server.valid_basic_auth("Basic dXNlcjpwYXNz"))
            os.environ["KIRA_ADMIN_USER"] = "user"
            os.environ["KIRA_ADMIN_PASSWORD"] = "pass"
            token = base64.b64encode(b"user:pass").decode("ascii")
            self.assertTrue(server.valid_basic_auth("Basic " + token))
            wrong = base64.b64encode(b"user:wrong").decode("ascii")
            self.assertFalse(server.valid_basic_auth("Basic " + wrong))
        finally:
            if old_user is not None:
                os.environ["KIRA_ADMIN_USER"] = old_user
            else:
                os.environ.pop("KIRA_ADMIN_USER", None)
            if old_password is not None:
                os.environ["KIRA_ADMIN_PASSWORD"] = old_password
            else:
                os.environ.pop("KIRA_ADMIN_PASSWORD", None)

    def test_agent_data_paths_are_configurable(self):
        self.assertTrue(str(server.agent.RESULTS).endswith("results.json"))
        self.assertEqual(Path(server.agent.RESULTS).parent, server.DATA_DIR)


if __name__ == "__main__":
    unittest.main()
