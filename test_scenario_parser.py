import unittest

from scenario_parser import SCENARIOS, parse_alert


class ScenarioParserTests(unittest.TestCase):
    def test_matches_scenario_keywords_and_returns_language_table(self):
        result = parse_alert(
            "FLASH FLOOD WARNING: water is rising; do not cross floodwater.",
            "te",
        )

        self.assertEqual(result["scenario"], "flood")
        self.assertEqual(
            result["matched_terms"],
            ["flash flood", "flood", "floodwater", "water is rising"],
        )
        self.assertEqual(result["native_language_name"], "Telugu")
        self.assertEqual(result["scenario_title"], "వరద హెచ్చరిక")
        self.assertTrue(result["recognized"])
        self.assertIn("వరద", result["short_text"])
        self.assertIn("high_ground", result["visual_steps"])

    def test_detects_each_supported_scenario(self):
        examples = {
            "fire": "Smoke and flames reported near the school",
            "storm": "Cyclone with high winds expected tonight",
            "earthquake": "Earthquake tremor reported",
            "medical": "Person unconscious after a heart attack",
            "heat": "Extreme heat and heatwave warning",
            "landslide": "Landslide on the road",
        }
        for expected, message in examples.items():
            with self.subTest(scenario=expected):
                self.assertEqual(parse_alert(message, "hi")["scenario"], expected)

    def test_unrecognized_message_uses_explicit_generic_scenario(self):
        result = parse_alert("Emergency in sector 4, follow official instructions", "bn")

        self.assertEqual(result["scenario"], "general")
        self.assertFalse(result["recognized"])
        self.assertEqual(result["matched_terms"], [])
        self.assertEqual(result["short_text"], SCENARIOS["general"]["messages"]["bn"])

    def test_rejects_empty_message_and_unsupported_language(self):
        with self.assertRaises(ValueError):
            parse_alert("  ", "hi")
        with self.assertRaises(ValueError):
            parse_alert("Flood warning", "kn")


if __name__ == "__main__":
    unittest.main()