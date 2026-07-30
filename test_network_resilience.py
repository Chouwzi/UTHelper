import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import unittest
from unittest.mock import patch, MagicMock
import urllib.error
import json
from core.client import MoodleClient

class TestNetworkResilience(unittest.TestCase):
    def setUp(self):
        self.client = MoodleClient()
        
    @patch("urllib.request.urlopen")
    def test_timeout(self, mock_urlopen):
        # Simulate a socket timeout
        mock_urlopen.side_effect = urllib.error.URLError("timed out")
        
        result = self.client.login("test", "test")
        self.assertFalse(result)
        print("Timeout Error Handled successfully (returned False)")
        
    @patch("urllib.request.urlopen")
    def test_http_500_error(self, mock_urlopen):
        # Simulate HTTP 500
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://test", 500, "Internal Server Error", {}, None
        )
        
        result = self.client.login("test", "test")
        self.assertFalse(result)
        print("HTTP 500 Error Handled successfully (returned False)")
        
    @patch("urllib.request.urlopen")
    def test_invalid_json_response(self, mock_urlopen):
        # Simulate 200 OK but HTML payload
        mock_response = MagicMock()
        mock_response.read.return_value = b"<html>Service Unavailable</html>"
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        result = self.client.login("test", "test")
        self.assertFalse(result)
        print("Invalid JSON Handled successfully (returned False)")

if __name__ == "__main__":
    unittest.main()
