"""Shorten and localize emergency alerts with Groq's chat completions API."""

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import load_root_env
from languages import SUPPORTED_LANGUAGES


VISUAL_ICONS = frozenset({
    "flood", "fire", "storm", "earthquake", "warning", "evacuate",
    "high_ground", "shelter", "medical", "no_cross", "call", "safe",
})
DEFAULT_VISUAL_STEPS = ["warning", "evacuate"]
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


load_root_env()


class LocalizationError(RuntimeError):
    """Raised when the configured language model cannot localize an alert."""


def unavailable_localizations(message: str, languages: list[str]) -> dict[str, dict[str, Any]]:
    return {
        language: {
            "short_text": message.strip(),
            "visual_steps": DEFAULT_VISUAL_STEPS.copy(),
            "localized": False,
        }
        for language in languages
    }


def _extract_json_object(content: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("No JSON object found in model response")


def _find_translations(payload: Any, languages: list[str]) -> dict[str, Any]:
    if isinstance(payload, dict):
        translations = payload.get("translations")
        if isinstance(translations, dict) and all(language in translations for language in languages):
            return translations
        if all(language in payload for language in languages):
            return payload
        for value in payload.values():
            try:
                return _find_translations(value, languages)
            except ValueError:
                continue
    elif isinstance(payload, list):
        for value in payload:
            try:
                return _find_translations(value, languages)
            except ValueError:
                continue
    raise ValueError("No translations found for all requested languages")


def localize_alert(message: str, languages: list[str]) -> dict[str, dict[str, Any]]:
    """Return concise native-language messages and pictogram tokens by language."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Alert message must not be empty")
    if len(message) > 2000:
        raise ValueError("Alert message must be 2000 characters or fewer")
    if not languages or any(language not in SUPPORTED_LANGUAGES for language in languages):
        raise ValueError("Choose one or more supported languages")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise LocalizationError("Groq localization is unavailable: GROQ_API_KEY is not set")

    language_names = {code: SUPPORTED_LANGUAGES[code] for code in languages}
    prompt = {
        "alert": message.strip(),
        "languages": language_names,
        "required_json_shape": {
            "translations": {
                code: {
                    "short_text": "Concise, accurate translation in the requested language",
                    "visual_steps": ["warning", "flood"],
                }
                for code in languages
            }
        },
        "allowed_visual_steps": sorted(VISUAL_ICONS),
    }
    body = json.dumps({
        "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
        "temperature": 0.2,
        "max_tokens": 700,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You prepare emergency alerts for SMS. Preserve every safety-critical fact, "
                    "location, timing, and instruction; never add facts. For each requested "
                    "language, shorten the alert to a clear, natural message of at most 240 "
                    "characters. Return only valid JSON matching required_json_shape. "
                    "visual_steps must contain 1 to 3 allowed icon tokens that depict the "
                    "hazard and action in order. Choose only from allowed_visual_steps."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }).encode("utf-8")
    request = Request(
        GROQ_ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "RelayLab/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=25) as response:
            completion = json.loads(response.read())
    except HTTPError as error:
        response_body = error.read()
        try:
            error_payload = json.loads(response_body)
        except (json.JSONDecodeError, AttributeError, TypeError):
            error_payload = {}
        provider_detail = (
            error_payload.get("error", error_payload.get("message", ""))
            if isinstance(error_payload, dict)
            else ""
        )
        if isinstance(provider_detail, dict):
            provider_error = provider_detail.get("message", "")
        elif isinstance(provider_detail, str):
            provider_error = provider_detail
        else:
            provider_error = ""
        if not provider_error and response_body:
            provider_error = response_body.decode("utf-8", errors="replace")
        if not isinstance(provider_error, str):
            provider_error = ""
        if api_key in provider_error:
            provider_error = provider_error.replace(api_key, "[redacted]")
        detail = f": {provider_error[:240]}" if provider_error else ""
        raise LocalizationError(
            f"Groq localization request failed (HTTP {error.code}){detail}"
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise LocalizationError("Could not reach Groq to localize this alert") from error

    content = ""
    try:
        content = completion["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("Model response content must be text")
        translations = _find_translations(_extract_json_object(content), languages)
        localized: dict[str, dict[str, Any]] = {}
        for language in languages:
            translated = translations[language]
            short_text = translated["short_text"].strip()
            visual_steps = translated["visual_steps"]
            if (
                not short_text
                or len(short_text) > 500
                or not isinstance(visual_steps, list)
                or not 1 <= len(visual_steps) <= 3
                or any(icon not in VISUAL_ICONS for icon in visual_steps)
            ):
                raise ValueError("Invalid localized message format")
            localized[language] = {
                "short_text": short_text,
                "visual_steps": visual_steps,
                "localized": True,
            }
        return localized
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        detail = str(error).replace(api_key, "[redacted]")[:160]
        if isinstance(content, str) and content:
            excerpt = " ".join(content.split()).replace(api_key, "[redacted]")[:160]
            detail = f"{detail}; response={excerpt}"
        raise LocalizationError(
            f"Groq returned an invalid alert format ({type(error).__name__}: {detail})"
        ) from error