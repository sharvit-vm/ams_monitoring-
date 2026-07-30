import unittest
from unittest.mock import patch

import requests

from db_fix.clients.servicenow_client import ServiceNowClient


class ServiceNowClientAuthTests(unittest.TestCase):
    @patch("db_fix.clients.servicenow_client.Settings")
    def test_client_uses_latest_settings(self, MockSettings):
        MockSettings.return_value = type(
            "Settings",
            (),
            {"SN_INSTANCE": "https://example.service-now.com/", "SN_USERNAME": "user", "SN_PASSWORD": "pass"},
        )()

        client = ServiceNowClient()

        self.assertEqual(client.base_url, "https://example.service-now.com")
        self.assertEqual(client.auth, ("user", "pass"))

    @patch("db_fix.clients.servicenow_client.requests.get")
    @patch("db_fix.clients.servicenow_client.Settings")
    def test_get_surfaces_auth_failure_clearly(self, MockSettings, mock_get):
        MockSettings.return_value = type(
            "Settings",
            (),
            {"SN_INSTANCE": "https://example.service-now.com/", "SN_USERNAME": "user", "SN_PASSWORD": "pass"},
        )()
        response = type("Resp", (), {"status_code": 401})()
        mock_get.side_effect = requests.exceptions.HTTPError(response=response)

        client = ServiceNowClient()

        with self.assertRaisesRegex(Exception, "authentication failed"):
            client.get("cmdb_ci_business_app", query="name=Insurance Portal")


if __name__ == "__main__":
    unittest.main()
