import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("provider_route", ROOT / "scripts" / "provider_route.py")
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)


class ProviderRouteTest(unittest.TestCase):
    def test_official_selected_without_thirdparty_access(self):
        with mock.patch.object(route, "status", side_effect=["configured"]) as status:
            with mock.patch.object(route, "probe", return_value={"status": "available"}) as probe:
                self.assertEqual(route.select()["provider"], "official")
        status.assert_called_once_with("official")
        probe.assert_called_once_with("official")

    def test_explicit_unavailability_requests_thirdparty_setup(self):
        with mock.patch.object(route, "status", side_effect=["configured", "missing"]):
            with mock.patch.object(route, "probe", return_value={"status": "unavailable", "reason": "model_missing"}):
                result = route.select()
        self.assertEqual(result["status"], "setup_required")
        self.assertEqual(result["provider"], "thirdparty")
        self.assertIn("credential-setup.ts thirdparty", result["setup_command"])

    def test_no_response_pauses_without_thirdparty_access(self):
        with mock.patch.object(route, "status", side_effect=["configured"]) as status:
            with mock.patch.object(route, "probe", return_value={"status": "pause"}):
                self.assertEqual(route.select()["status"], "pause")
        status.assert_called_once_with("official")

    def test_thirdparty_unauthorized_explains_credential_replacement(self):
        with mock.patch.object(route, "status", side_effect=["missing", "configured"]):
            with mock.patch.object(route, "probe", return_value={"status": "unavailable", "reason": "HTTP 401"}):
                result = route.select()
        self.assertEqual(result["status"], "pause")
        self.assertIn("HTTP 401", result["reason"])
        self.assertIn("开发者 SK", result["reason"])


if __name__ == "__main__":
    unittest.main()
