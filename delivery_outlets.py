"""Optional real-device delivery adapters used only after explicit opt-in."""

import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class OutletError(RuntimeError):
    """Raised when an external delivery outlet cannot send a message."""


def send_twilio_sms(phone_number: str, message: str) -> str:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
    if not all((account_sid, auth_token, from_number)):
        raise OutletError(
            "Twilio SMS is not configured; set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER in .env"
        )

    endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    token = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    request = Request(
        endpoint,
        data=urlencode({"To": phone_number, "From": from_number, "Body": message}).encode(),
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "RelayLab/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        raise OutletError(f"Twilio rejected the SMS (HTTP {error.code})") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise OutletError("Could not reach the Twilio SMS service") from error

    message_sid = result.get("sid")
    if not isinstance(message_sid, str) or not message_sid.startswith("SM"):
        raise OutletError("Twilio returned an invalid SMS receipt")
    return message_sid


def send_web_push(subscription: dict, payload: dict) -> None:
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "")
    subject = os.environ.get("VAPID_SUBJECT", "")
    if not private_key or not subject:
        raise OutletError("Web Push is not configured; set VAPID_PRIVATE_KEY and VAPID_SUBJECT in .env")

    try:
        from pywebpush import WebPushException, webpush
    except ImportError as error:
        raise OutletError("Web Push dependency missing; install requirements.txt") from error

    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},
            ttl=60,
            timeout=15,
        )
    except WebPushException as error:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code in (404, 410):
            raise OutletError("Push subscription expired") from error
        if status_code:
            raise OutletError(f"Push service rejected notification (HTTP {status_code})") from error
        raise OutletError("Could not send browser push notification") from error
    except (TimeoutError, OSError) as error:
        raise OutletError("Could not reach the browser push service") from error