import unittest
from types import SimpleNamespace
from unittest.mock import patch

import webapp
from scenario_parser import parse_alert


class WebappLocalizationTests(unittest.TestCase):
    def setUp(self):
        self.simulation_map = webapp._new_map()
        self.handler = SimpleNamespace(path="/api/deliver")
        self.payload = {
            "sender_id": "Alert source",
            "payload_bytes": 26,
            "alert_message": "Move to higher ground now.",
            "failure_rate": 0,
            "max_retries": 1,
        }

    def test_broadcast_uses_scenario_table_for_each_recipient(self):
        with patch.object(webapp, "_simulation_map", self.simulation_map), patch.object(
            webapp, "_device_inbox", {}
        ):
            report = webapp.SimulatorHandler._dispatch_post(self.handler, self.payload)

        by_id = {delivery["receiver_id"]: delivery for delivery in report["deliveries"]}
        expected_bengali = parse_alert(self.payload["alert_message"], "bn")
        self.assertEqual(by_id["Village relay"]["native_language"], "bn")
        self.assertEqual(by_id["Village relay"]["received_text"], expected_bengali["short_text"])
        self.assertEqual(by_id["Village relay"]["scenario"], "general")
        self.assertFalse(by_id["Village relay"]["recognized_scenario"])
        self.assertEqual(by_id["Health post"]["visual_steps"], expected_bengali["visual_steps"])
        self.assertEqual(report["alert_message"], self.payload["alert_message"])
        self.assertNotIn("localization_error", report)

    def test_broadcast_classifies_flood_and_enqueues_inbox_message(self):
        self.payload["alert_message"] = "FLASH FLOOD: water is rising. Move now."
        with patch.object(webapp, "_simulation_map", self.simulation_map), patch.object(
            webapp, "_device_inbox", {}
        ) as inbox:
            report = webapp.SimulatorHandler._dispatch_post(self.handler, self.payload)

        nearby = next(item for item in report["deliveries"] if item["receiver_id"] == "Nearby household")
        self.assertEqual(nearby["scenario"], "flood")
        self.assertTrue(nearby["recognized_scenario"])
        self.assertEqual(inbox["Nearby household"][0]["text"], nearby["received_text"])
        self.assertEqual(inbox["Nearby household"][0]["native_language"], "hi")

    def test_preview_endpoint_returns_deterministic_summary(self):
        handler = SimpleNamespace(path="/api/preview")
        result = webapp.SimulatorHandler._dispatch_post(
            handler,
            {"alert_message": "Wildfire smoke is nearby", "native_language": "ta"},
        )

        self.assertEqual(result["scenario"], "fire")
        self.assertIn("தீ", result["short_text"])

    def test_device_language_can_be_updated(self):
        handler = SimpleNamespace(path="/api/devices/language")
        with patch.object(webapp, "_simulation_map", self.simulation_map):
            state = webapp.SimulatorHandler._dispatch_post(
                handler,
                {"device_id": "Village relay", "native_language": "te"},
            )

        relay = next(device for device in state["devices"] if device["id"] == "Village relay")
        self.assertEqual(relay["native_language"], "te")
        self.assertEqual(relay["native_language_name"], "Telugu")

    def test_device_phone_can_be_saved(self):
        handler = SimpleNamespace(path="/api/devices/phone")
        with patch.object(webapp, "_simulation_map", self.simulation_map):
            state = webapp.SimulatorHandler._dispatch_post(
                handler,
                {"device_id": "Village relay", "phone_number": "+919876543210"},
            )

        relay = next(device for device in state["devices"] if device["id"] == "Village relay")
        self.assertEqual(relay["phone_number"], "+919876543210")

    def test_explicit_real_sms_opt_in_calls_gateway_for_acknowledged_recipient(self):
        handler = SimpleNamespace(path="/api/devices/phone")
        with patch.object(webapp, "_simulation_map", self.simulation_map), patch.object(
            webapp, "_device_inbox", {}
        ), patch.object(webapp, "send_twilio_sms", return_value="SM-test") as send_sms:
            webapp.SimulatorHandler._dispatch_post(
                handler,
                {"device_id": "Nearby household", "phone_number": "+919876543210"},
            )
            report = webapp.SimulatorHandler._dispatch_post(
                self.handler,
                {**self.payload, "send_sms": True},
            )

        nearby = next(item for item in report["deliveries"] if item["receiver_id"] == "Nearby household")
        send_sms.assert_called_once_with("+919876543210", nearby["received_text"])
        self.assertEqual(nearby["sms_status"], "accepted by gateway")
        self.assertEqual(nearby["sms_message_sid"], "SM-test")

    def test_push_subscription_is_saved_for_a_known_device(self):
        handler = SimpleNamespace(path="/api/push/subscribe")
        subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
            "keys": {"p256dh": "public", "auth": "secret"},
        }
        with patch.object(webapp, "_simulation_map", self.simulation_map), patch.object(
            webapp, "_push_subscriptions", {}
        ) as subscriptions, patch.object(webapp, "_save_push_subscriptions"):
            result = webapp.SimulatorHandler._dispatch_post(
                handler,
                {"device_id": "Nearby household", "subscription": subscription},
            )

        self.assertTrue(result["saved"])
        self.assertEqual(subscriptions["Nearby household"], [subscription])

    def test_invalid_push_subscription_is_rejected(self):
        handler = SimpleNamespace(path="/api/push/subscribe")
        with patch.object(webapp, "_simulation_map", self.simulation_map):
            with self.assertRaisesRegex(ValueError, "HTTPS endpoint"):
                webapp.SimulatorHandler._dispatch_post(
                    handler,
                    {
                        "device_id": "Nearby household",
                        "subscription": {"endpoint": "http://bad", "keys": {}},
                    },
                )

    def test_acknowledged_broadcast_sends_push_to_registered_device(self):
        subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
            "keys": {"p256dh": "public", "auth": "secret"},
        }
        with patch.object(webapp, "_simulation_map", self.simulation_map), patch.object(
            webapp, "_push_subscriptions", {"Nearby household": [subscription]}
        ), patch.object(webapp, "_device_inbox", {}), patch.object(
            webapp, "_save_push_subscriptions"
        ), patch.object(webapp, "send_web_push") as send_push:
            report = webapp.SimulatorHandler._dispatch_post(self.handler, {
                **self.payload,
                "alert_message": "Flash flood warning now",
            })

        nearby = next(item for item in report["deliveries"] if item["receiver_id"] == "Nearby household")
        send_push.assert_called_once()
        pushed_message = send_push.call_args.args[1]
        self.assertEqual(pushed_message["title"], "बाढ़ चेतावनी")
        self.assertEqual(pushed_message["body"], nearby["received_text"])
        self.assertEqual(nearby["push_status"], "sent to 1 subscription(s)")

    def test_failed_simulated_delivery_does_not_send_push(self):
        subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
            "keys": {"p256dh": "public", "auth": "secret"},
        }
        with patch.object(webapp, "_simulation_map", self.simulation_map), patch.object(
            webapp, "_push_subscriptions", {"Nearby household": [subscription]}
        ), patch.object(webapp, "_device_inbox", {}), patch.object(
            webapp, "_save_push_subscriptions"
        ), patch.object(webapp, "send_web_push") as send_push:
            report = webapp.SimulatorHandler._dispatch_post(self.handler, {
                **self.payload,
                "failure_rate": 1,
                "max_retries": 0,
            })

        nearby = next(item for item in report["deliveries"] if item["receiver_id"] == "Nearby household")
        send_push.assert_not_called()
        self.assertEqual(nearby["push_status"], "not sent: simulated delivery failed")


if __name__ == "__main__":
    unittest.main()