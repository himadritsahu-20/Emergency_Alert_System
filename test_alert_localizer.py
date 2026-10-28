import json
import os
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from alert_localizer import LocalizationError, localize_alert, unavailable_localizations
from config import load_root_env


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class AlertLocalizerTests(unittest.TestCase):
    def test_formats_requested_languages_and_validates_visual_steps(self) -> None:
        model_result = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "translations": {
                            "hi": {"short_text": "ऊँची जगह जाएँ।", "visual_steps": ["flood", "high_ground"]},
                            "ta": {"short_text": "உயரமான இடத்திற்குச் செல்லுங்கள்.", "visual_steps": ["warning", "evacuate"]},
                        }
                    })
                }
            }]
        }
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch(
            "alert_localizer.urlopen", return_value=FakeResponse(model_result)
        ) as open_url:
            result = localize_alert("Move to higher ground now.", ["hi", "ta"])

        self.assertTrue(result["hi"]["localized"])
        self.assertEqual(result["ta"]["visual_steps"], ["warning", "evacuate"])
        request = open_url.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(sent["model"], "openai/gpt-oss-20b")
        self.assertNotIn("response_format", sent)
        self.assertEqual(request.get_header("User-agent"), "RelayLab/1.0")
        self.assertIn("hi", sent["messages"][1]["content"])

    def test_rejects_unknown_visual_tokens(self) -> None:
        model_result = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "translations": {
                            "bn": {"short_text": "বার্তা", "visual_steps": ["invented_icon"]}
                        }
                    })
                }
            }]
        }
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch(
            "alert_localizer.urlopen", return_value=FakeResponse(model_result)
        ):
            with self.assertRaisesRegex(LocalizationError, "invalid alert format"):
                localize_alert("Flood warning", ["bn"])

    def test_accepts_json_wrapped_in_markdown_or_surrounding_text(self) -> None:
        translation_json = json.dumps({
            "translations": {
                "hi": {"short_text": "ऊँची जगह जाएँ।", "visual_steps": ["flood", "high_ground"]}
            }
        })
        model_result = {
            "choices": [{
                "message": {"content": f"Here is the alert:\n```json\n{translation_json}\n```"}
            }]
        }
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch(
            "alert_localizer.urlopen", return_value=FakeResponse(model_result)
        ):
            result = localize_alert("Move to higher ground now.", ["hi"])

        self.assertEqual(result["hi"]["short_text"], "ऊँची जगह जाएँ।")
        self.assertTrue(result["hi"]["localized"])

    def test_finds_language_map_nested_in_response_envelope(self) -> None:
        model_result = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "result": {
                            "translations": {
                                "hi": {
                                    "short_text": "ऊँची जगह जाएँ।",
                                    "visual_steps": ["warning", "high_ground"],
                                }
                            }
                        }
                    })
                }
            }]
        }
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch(
            "alert_localizer.urlopen", return_value=FakeResponse(model_result)
        ):
            result = localize_alert("Move to higher ground.", ["hi"])

        self.assertEqual(result["hi"]["short_text"], "ऊँची जगह जाएँ।")

    def test_provider_http_error_is_reported_without_exposing_the_key(self) -> None:
        key = "test-secret-key"
        error_body = json.dumps({"error": {"message": "Credential denied"}}).encode()
        provider_error = HTTPError(
            "https://api.groq.com/", 403, "Forbidden", None, BytesIO(error_body)
        )
        with patch.dict(os.environ, {"GROQ_API_KEY": key}), patch(
            "alert_localizer.urlopen", side_effect=provider_error
        ):
            with self.assertRaisesRegex(
                LocalizationError,
                r"HTTP 403\): Credential denied",
            ) as error:
                localize_alert("Alert", ["hi"])

        self.assertNotIn(key, str(error.exception))

    def test_provider_error_accepts_string_json_and_redacts_plain_text(self) -> None:
        key = "test-secret-key"
        cases = (
            (json.dumps({"error": "Credential denied"}).encode(), "Credential denied"),
            (f"Access denied for {key}".encode(), "Access denied for [redacted]"),
        )
        with patch.dict(os.environ, {"GROQ_API_KEY": key}):
            for body, expected in cases:
                provider_error = HTTPError(
                    "https://api.groq.com/", 403, "Forbidden", None, BytesIO(body)
                )
                with self.subTest(expected=expected), patch(
                    "alert_localizer.urlopen", side_effect=provider_error
                ):
                    with self.assertRaises(LocalizationError) as error:
                        localize_alert("Alert", ["hi"])
                    self.assertIn(expected, str(error.exception))
                    self.assertNotIn(key, str(error.exception))

    def test_requires_groq_key_and_builds_explicit_fallback(self) -> None:
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            with self.assertRaisesRegex(LocalizationError, "GROQ_API_KEY"):
                localize_alert("Alert", ["te"])

        fallback = unavailable_localizations("Move now.", ["te"])
        self.assertEqual(fallback["te"]["short_text"], "Move now.")
        self.assertFalse(fallback["te"]["localized"])

    def test_rejects_unsupported_language(self) -> None:
        with self.assertRaisesRegex(ValueError, "supported languages"):
            localize_alert("Alert", ["kn"])

    def test_loads_root_env_values_without_overriding_process_values(self) -> None:
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                'GROQ_API_KEY="test-only-key"\nGROQ_MODEL=test-model # local default\n',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"GROQ_API_KEY": "stale-process-key", "GROQ_MODEL": "process-model"},
                clear=True,
            ):
                load_root_env(env_path)

                self.assertEqual(os.environ["GROQ_API_KEY"], "test-only-key")
                self.assertEqual(os.environ["GROQ_MODEL"], "process-model")


if __name__ == "__main__":
    unittest.main()