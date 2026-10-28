import unittest

from simulator import BandwidthProfile, Device, Map, simulate_delivery


class SimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.simulation_map = Map(1000, 1000)
        self.sender = Device("sender", 10, 20)
        self.receiver = Device("receiver", 15, 24)
        self.simulation_map.add_device(self.sender)
        self.simulation_map.add_device(self.receiver)

    def test_uses_bluetooth_when_devices_are_in_range(self) -> None:
        transmission = self.simulation_map.send(self.sender, self.receiver, 100)

        self.assertEqual(transmission.channel, "bluetooth")
        self.assertAlmostEqual(transmission.distance_m, 41 ** 0.5)
        self.assertGreater(transmission.estimated_seconds, 0)

    def test_falls_back_to_2g_outside_bluetooth_range(self) -> None:
        far_receiver = Device("far", 100, 100)
        self.simulation_map.add_device(far_receiver)

        transmission = self.simulation_map.send(self.sender, far_receiver, 100)

        self.assertEqual(transmission.channel, "2g")

    def test_uses_3g_when_2g_is_unavailable(self) -> None:
        self.simulation_map.available_cellular_generations = frozenset({"3g"})
        far_receiver = Device("far", 100, 100)
        self.simulation_map.add_device(far_receiver)

        transmission = self.simulation_map.send(self.sender, far_receiver, 100)

        self.assertEqual(transmission.channel, "3g")

    def test_rejects_device_outside_map(self) -> None:
        with self.assertRaises(ValueError):
            self.simulation_map.add_device(Device("outside", 1001, 5))

    def test_accepts_international_device_phone_number(self) -> None:
        device = Device("sms-device", 10, 10, phone_number="+919876543210")

        self.assertEqual(device.phone_number, "+919876543210")

    def test_rejects_phone_number_that_is_not_e164(self) -> None:
        with self.assertRaisesRegex(ValueError, "E.164"):
            Device("sms-device", 10, 10, phone_number="9876543210")

    def test_rejects_invalid_triangular_bandwidth_profile(self) -> None:
        with self.assertRaises(ValueError):
            BandwidthProfile(20, 10, 30)

    def test_fails_when_no_shared_route_is_available(self) -> None:
        isolated_map = Map(1000, 1000, available_cellular_generations=frozenset())
        sender = Device("isolated-sender", 10, 20, bluetooth_enabled=False)
        receiver = Device("isolated-receiver", 15, 24, bluetooth_enabled=False)
        isolated_map.add_device(sender)
        isolated_map.add_device(receiver)

        with self.assertRaises(ConnectionError):
            isolated_map.send(sender, receiver, 100)

    def test_delivery_is_acknowledged_on_first_attempt(self) -> None:
        outcome = simulate_delivery(0.25, 2, random_value=lambda: 0.9)

        self.assertEqual(outcome.status, "acknowledged")
        self.assertTrue(outcome.acknowledged)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.retries, 0)

    def test_delivery_retries_after_failure_until_acknowledged(self) -> None:
        values = iter((0.1, 0.9))
        outcome = simulate_delivery(0.5, 2, random_value=lambda: next(values))

        self.assertEqual(outcome.status, "acknowledged")
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(outcome.retries, 1)

    def test_delivery_fails_after_retry_limit(self) -> None:
        outcome = simulate_delivery(1, 2, random_value=lambda: 0.5)

        self.assertEqual(outcome.status, "failed")
        self.assertFalse(outcome.acknowledged)
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(outcome.retries, 2)


if __name__ == "__main__":
    unittest.main()