import json
import os
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from delivery_outlets import OutletError, send_twilio_sms, send_web_push


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps({"sid": "SM1234567890"}).encode("utf-8")


class DeliveryOutletTests(unittest.TestCase):
    def test_twilio_uses_configured_e164_target_and_utf8_message(self):
        settings = {
            "TWILIO_ACCOUNT_SID": "AC-test-account",
            "TWILIO_AUTH_TOKEN": "test-token",
            "TWILIO_FROM_NUMBER": "+15550000000",
        }
        with patch.dict(os.environ, settings), patch(
            "delivery_outlets.urlopen", return_value=FakeResponse()
        ) as open_url:
            message_sid = send_twilio_sms("+919876543210", "बाढ़ का खतरा।")

        request = open_url.call_args.args[0]
        self.assertEqual(message_sid, "SM1234567890")
        self.assertIn("/Accounts/AC-test-account/Messages.json", request.full_url)
        self.assertNotIn("test-token", request.data.decode("ascii"))
        self.assertIn("Authorization", request.headers)
        self.assertIn(b"Body=%E0", request.data)

    def test_twilio_requires_all_three_settings(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(OutletError, "TWILIO_ACCOUNT_SID"):
                send_twilio_sms("+919876543210", "Test alert")

    def test_web_push_uses_vapid_and_passes_payload_to_library(self):
        fake_module = ModuleType("pywebpush")
        fake_module.WebPushException = type("WebPushException", (Exception,), {})
        fake_module.webpush = Mock(return_value="sent")
        settings = {
            "VAPID_PRIVATE_KEY": "/tmp/test-private.pem",
            "VAPID_SUBJECT": "mailto:test@example.test",
        }
        with patch.dict(os.environ, settings), patch.dict(sys.modules, {"pywebpush": fake_module}):
            send_web_push({"endpoint": "https://push.example.test/sub"}, {"body": "Alert"})

        fake_module.webpush.assert_called_once()
        arguments = fake_module.webpush.call_args.kwargs
        self.assertEqual(arguments["vapid_private_key"], settings["VAPID_PRIVATE_KEY"])
        self.assertEqual(arguments["vapid_claims"], {"sub": settings["VAPID_SUBJECT"]})

    def test_web_push_reports_missing_vapid_settings(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(OutletError, "VAPID_PRIVATE_KEY"):
                send_web_push({}, {})


if __name__ == "__main__":
    unittest.main()
