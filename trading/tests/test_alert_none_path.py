"""Test for alert and log_alert with alerts_path=None."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from trading.execution.alerting import alert, log_alert


class TestAlertNonePath(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory to serve as HOME
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        # Patch the environment variable HOME so that DEFAULT_ALERTS_PATH points to our temp dir
        self.patcher = patch.dict(os.environ, {'HOME': self.temp_dir.name})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

        # We need to reload the module to pick up the new HOME? Actually, DEFAULT_ALERTS_PATH is computed at import time.
        # Since we already imported the module, we need to patch the constant in the module.
        # We'll do it in each test by patching 'trading.execution.alerting.DEFAULT_ALERTS_PATH'

    def test_alert_with_none_path_returns_record_without_raising(self):
        """alert(message, alerts_path=None) should return a dict and not raise."""
        with patch('trading.execution.alerting.DEFAULT_ALERTS_PATH',
                   os.path.join(self.temp_dir.name, '.trading', 'execution', 'alerts.log')):
            # Call alert with alerts_path=None
            result = alert("test message", severity="CRITICAL", alerts_path=None)
            # Should return a dict
            self.assertIsInstance(result, dict)
            self.assertIn('ts', result)
            self.assertEqual(result['message'], "test message")
            self.assertEqual(result['severity'], "CRITICAL")

    def test_log_alert_with_none_path_writes_to_default_location(self):
        """log_alert(..., alerts_path=None) should write to the default alerts.log."""
        expected_path = os.path.join(self.temp_dir.name, '.trading', 'execution', 'alerts.log')
        with patch('trading.execution.alerting.DEFAULT_ALERTS_PATH', expected_path):
            # Call log_alert with alerts_path=None
            log_alert("test message", severity="WARN", alerts_path=None)
            # Check that the file was created and contains the expected line
            self.assertTrue(os.path.exists(expected_path))
            with open(expected_path, 'r') as f:
                line = f.readline().strip()
                self.assertTrue(line.startswith('{"ts":'))
                # Parse the JSON and check the message
                data = json.loads(line)
                self.assertEqual(data['message'], "test message")
                self.assertEqual(data['severity'], "WARN")


if __name__ == '__main__':
    unittest.main()