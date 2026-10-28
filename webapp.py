"""Run the browser-based low-bandwidth delivery simulator."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import ssl
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from config import load_root_env
from delivery_outlets import OutletError, send_twilio_sms, send_web_push
from languages import SUPPORTED_LANGUAGES
from scenario_parser import parse_alert
from simulator import Device, Map, simulate_delivery


load_root_env()
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
MAP_WIDTH_M = 1000
MAP_HEIGHT_M = 680
STATIC_DIR = Path(__file__).parent / "static"
PUSH_SUBSCRIPTIONS_FILE = Path(__file__).parent / "data" / "push_subscriptions.json"
_lock = RLock()
_device_inbox: dict[str, list[dict[str, Any]]] = {}


def _load_push_subscriptions() -> dict[str, list[dict[str, Any]]]:
    try:
        stored = json.loads(PUSH_SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {
        device_id: subscriptions
        for device_id, subscriptions in stored.items()
        if isinstance(device_id, str)
        and isinstance(subscriptions, list)
        and all(isinstance(subscription, dict) for subscription in subscriptions)
    }


def _save_push_subscriptions() -> None:
    PUSH_SUBSCRIPTIONS_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    PUSH_SUBSCRIPTIONS_FILE.parent.chmod(0o700)
    temporary_file = PUSH_SUBSCRIPTIONS_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(_push_subscriptions, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_file.chmod(0o600)
    temporary_file.replace(PUSH_SUBSCRIPTIONS_FILE)


_push_subscriptions = _load_push_subscriptions()


def _new_map() -> Map:
    simulation_map = Map(MAP_WIDTH_M, MAP_HEIGHT_M)
    for device in (
        Device("Alert source", 160, 160, bluetooth_range_m=60, native_language="hi"),
        Device("Nearby household", 200, 195, bluetooth_range_m=60, native_language="hi"),
        Device("Village relay", 395, 275, native_language="bn"),
        Device("Riverside home", 655, 430, native_language="ta"),
        Device("Health post", 835, 185, native_language="te"),
    ):
        simulation_map.add_device(device)
    return simulation_map


_simulation_map = _new_map()


def _device_data(device: Device) -> dict[str, Any]:
    return {
        "id": device.device_id,
        "x": device.x,
        "y": device.y,
        "bluetooth_range_m": device.bluetooth_range_m,
        "bluetooth_enabled": device.bluetooth_enabled,
        "cellular_generations": sorted(device.cellular_generations),
        "native_language": device.native_language,
        "native_language_name": SUPPORTED_LANGUAGES[device.native_language],
        "phone_number": device.phone_number,
    }


def _state_data() -> dict[str, Any]:
    return {
        "width_m": _simulation_map.width_m,
        "height_m": _simulation_map.height_m,
        "available_cellular_generations": sorted(
            _simulation_map.available_cellular_generations
        ),
        "native_languages": SUPPORTED_LANGUAGES,
        "devices": [_device_data(device) for device in _simulation_map.devices],
    }


class SimulatorHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:
        request_path = urlsplit(self.path).path
        if request_path == "/api/state":
            with _lock:
                self._send_json(200, _state_data())
            return
        if request_path == "/api/inbox":
            device_id = self._query_value("device_id")
            with _lock:
                if not any(device.device_id == device_id for device in _simulation_map.devices):
                    self._send_json(404, {"error": "Device was not found"})
                    return
                messages = _device_inbox.get(device_id, [])
                _device_inbox[device_id] = []
                self._send_json(200, {"messages": messages})
            return
        if request_path == "/api/push/config":
            with _lock:
                public_key = os.environ.get("VAPID_PUBLIC_KEY", "")
                private_key = Path(os.environ.get("VAPID_PRIVATE_KEY", ""))
                self._send_json(200, {
                    "enabled": bool(public_key and private_key.is_file() and os.environ.get("VAPID_SUBJECT")),
                    "public_key": public_key or None,
                    "https_required": True,
                })
            return
        super().do_GET()

    def _query_value(self, name: str) -> str:
        values = parse_qs(urlsplit(self.path).query).get(name, [])
        return values[0] if values else ""

    def do_POST(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length > 100_000:
                raise ValueError("Request body is too large")
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            with _lock:
                result = self._dispatch_post(payload, defer_outlets=True)
            pending_outlets = result.pop("_pending_outlets", [])
            for outlet in pending_outlets:
                delivery = outlet["delivery"]
                try:
                    if outlet["type"] == "sms":
                        message_sid = send_twilio_sms(outlet["phone_number"], outlet["message"])
                        delivery["sms_status"] = "accepted by gateway"
                        delivery["sms_message_sid"] = message_sid
                    else:
                        send_web_push(outlet["subscription"], outlet["payload"])
                        outlet["results"].append("sent")
                except OutletError as error:
                    if outlet["type"] == "sms":
                        delivery["sms_status"] = str(error)
                    else:
                        outlet["results"].append(str(error))
                        if str(error) == "Push subscription expired":
                            with _lock:
                                device_id = delivery["receiver_id"]
                                _push_subscriptions[device_id] = [
                                    saved for saved in _push_subscriptions.get(device_id, [])
                                    if saved.get("endpoint") != outlet["subscription"].get("endpoint")
                                ]
                                if not _push_subscriptions[device_id]:
                                    _push_subscriptions.pop(device_id, None)
                                _save_push_subscriptions()
            for outlet in pending_outlets:
                if outlet["type"] == "push":
                    results = outlet["results"]
                    outlet["delivery"]["push_status"] = (
                        f"sent to {results.count('sent')} subscription(s)"
                        if "sent" in results
                        else "; ".join(dict.fromkeys(results))
                    )
            self._send_json(200, result)
        except (json.JSONDecodeError, ValueError, TypeError, KeyError, ConnectionError) as error:
            self._send_json(400, {"error": str(error)})

    def _dispatch_post(
        self,
        payload: dict[str, Any],
        defer_outlets: bool = False,
    ) -> dict[str, Any]:
        global _simulation_map

        if self.path == "/api/reset":
            _simulation_map = _new_map()
            _device_inbox.clear()
            return _state_data()

        if self.path == "/api/preview":
            message = payload.get("alert_message", "")
            language = payload.get("native_language", "hi")
            return parse_alert(message, language)

        if self.path == "/api/devices":
            device_id = str(payload.get("id", "")).strip()
            device = Device(
                device_id=device_id,
                x=float(payload["x"]),
                y=float(payload["y"]),
                native_language=str(payload.get("native_language", "hi")),
            )
            _simulation_map.add_device(device)
            return {"device": _device_data(device), "state": _state_data()}

        if self.path == "/api/devices/language":
            device_id = payload["device_id"]
            language = payload["native_language"]
            device = next(
                (device for device in _simulation_map.devices if device.device_id == device_id),
                None,
            )
            if device is None:
                raise ValueError("Select a device that is on the map")
            if language not in SUPPORTED_LANGUAGES:
                raise ValueError("Choose a supported language")
            _simulation_map.devices[_simulation_map.devices.index(device)] = replace(
                device,
                native_language=language,
            )
            return _state_data()

        if self.path == "/api/devices/phone":
            device_id = payload["device_id"]
            phone_number = str(payload.get("phone_number", "")).strip()
            device = next(
                (device for device in _simulation_map.devices if device.device_id == device_id),
                None,
            )
            if device is None:
                raise ValueError("Select a device that is on the map")
            updated = replace(device, phone_number=phone_number)
            _simulation_map.devices[_simulation_map.devices.index(device)] = updated
            return _state_data()

        if self.path == "/api/push/subscribe":
            device_id = str(payload.get("device_id", ""))
            subscription = payload.get("subscription")
            if not any(device.device_id == device_id for device in _simulation_map.devices):
                raise ValueError("Select a device that is on the map")
            if not isinstance(subscription, dict):
                raise ValueError("Invalid push subscription")
            endpoint = subscription.get("endpoint")
            endpoint_host = urlsplit(endpoint).hostname if isinstance(endpoint, str) else None
            keys = subscription.get("keys")
            if (
                not isinstance(endpoint, str)
                or not endpoint.startswith("https://")
                or len(endpoint) > 2048
                or not endpoint_host
                or not (
                    endpoint_host == "fcm.googleapis.com"
                    or endpoint_host == "updates.push.services.mozilla.com"
                    or endpoint_host == "web.push.apple.com"
                    or endpoint_host.endswith(".notify.windows.com")
                )
                or not isinstance(keys, dict)
                or not isinstance(keys.get("p256dh"), str)
                or not isinstance(keys.get("auth"), str)
            ):
                raise ValueError("Push subscription must include an HTTPS endpoint and encryption keys")
            subscriptions = _push_subscriptions.setdefault(device_id, [])
            subscriptions[:] = [item for item in subscriptions if item.get("endpoint") != endpoint]
            subscriptions.append(subscription)
            _save_push_subscriptions()
            return {"saved": True, "subscription_count": len(subscriptions)}

        if self.path == "/api/push/unsubscribe":
            device_id = str(payload.get("device_id", ""))
            endpoint = payload.get("endpoint")
            subscriptions = _push_subscriptions.get(device_id, [])
            subscriptions[:] = [item for item in subscriptions if item.get("endpoint") != endpoint]
            if not subscriptions:
                _push_subscriptions.pop(device_id, None)
            _save_push_subscriptions()
            return {"saved": True}

        if self.path == "/api/networks":
            generations = frozenset(payload.get("available_cellular_generations", []))
            if not generations <= {"2g", "3g"}:
                raise ValueError("Only 2g and 3g are supported")
            _simulation_map.available_cellular_generations = generations
            return _state_data()

        if self.path == "/api/deliver":
            sender_id = payload["sender_id"]
            sender = next(
                (device for device in _simulation_map.devices if device.device_id == sender_id),
                None,
            )
            if sender is None:
                raise ValueError("Select a source device that is on the map")
            payload_bytes = payload["payload_bytes"]
            if not isinstance(payload_bytes, int) or payload_bytes < 0:
                raise ValueError("payload_bytes must be a non-negative integer")
            alert_message = payload.get("alert_message", "")
            if not isinstance(alert_message, str) or not alert_message.strip():
                raise ValueError("Alert message must not be empty")
            if len(alert_message) > 2000:
                raise ValueError("Alert message must be 2000 characters or fewer")
            failure_rate = float(payload.get("failure_rate", 0))
            send_sms = payload.get("send_sms") is True
            max_retries = payload.get("max_retries", 0)
            if not isinstance(max_retries, int) or max_retries < 0 or max_retries > 5:
                raise ValueError("max_retries must be an integer between 0 and 5")

            deliveries = []
            pending_outlets = []
            for receiver in _simulation_map.devices:
                if receiver == sender:
                    continue
                localized = parse_alert(alert_message, receiver.native_language)
                received_text = localized["short_text"]
                received_bytes = len(received_text.encode("utf-8"))
                try:
                    transmission = _simulation_map.send(sender, receiver, received_bytes)
                    outcome = simulate_delivery(failure_rate, max_retries)
                    deliveries.append(
                        {
                            "receiver_id": receiver.device_id,
                            "status": outcome.status,
                            "acknowledged": outcome.acknowledged,
                            "attempts": outcome.attempts,
                            "retries": outcome.retries,
                            "channel": transmission.channel,
                            "distance_m": round(transmission.distance_m, 1),
                            "estimated_seconds": transmission.estimated_seconds * outcome.attempts,
                            "received_bytes": received_bytes,
                            "native_language": receiver.native_language,
                            "native_language_name": SUPPORTED_LANGUAGES[receiver.native_language],
                            "received_text": received_text,
                            "visual_steps": localized["visual_steps"],
                            "localized": True,
                            "scenario": localized["scenario"],
                            "scenario_name": localized["scenario_name"],
                            "scenario_title": localized["scenario_title"],
                            "matched_terms": localized["matched_terms"],
                            "recognized_scenario": localized["recognized"],
                        }
                    )
                    sms_status = "not requested"
                    sms_message_sid = None
                    push_status = "not subscribed"
                    if outcome.acknowledged:
                        message = {
                            "id": str(uuid4()),
                            "received_at": datetime.now(timezone.utc).isoformat(),
                            "sender_id": sender_id,
                            "scenario": localized["scenario"],
                            "scenario_title": localized["scenario_title"],
                            "text": received_text,
                            "visual_steps": localized["visual_steps"],
                            "native_language": receiver.native_language,
                        }
                        _device_inbox.setdefault(receiver.device_id, []).append(message)
                        subscriptions = list(_push_subscriptions.get(receiver.device_id, []))
                        if subscriptions:
                            push_results = []
                            for subscription in subscriptions:
                                push_payload = {
                                    "id": message["id"],
                                    "device_id": receiver.device_id,
                                    "title": message["scenario_title"],
                                    "body": message["text"],
                                    "text": message["text"],
                                    "visual_steps": message["visual_steps"],
                                    "native_language": message["native_language"],
                                    "url": f"/receiver.html?device_id={receiver.device_id}",
                                }
                                if defer_outlets:
                                    pending_outlets.append({
                                        "type": "push",
                                        "delivery": deliveries[-1],
                                        "subscription": subscription,
                                        "payload": push_payload,
                                        "results": push_results,
                                    })
                                else:
                                    try:
                                        send_web_push(subscription, push_payload)
                                        push_results.append("sent")
                                    except OutletError as error:
                                        push_results.append(str(error))
                                        if str(error) == "Push subscription expired":
                                            _push_subscriptions[receiver.device_id] = [
                                                saved for saved in _push_subscriptions.get(receiver.device_id, [])
                                                if saved.get("endpoint") != subscription.get("endpoint")
                                            ]
                            if not defer_outlets:
                                _save_push_subscriptions()
                            push_status = "queued" if defer_outlets else (
                                f"sent to {push_results.count('sent')} subscription(s)"
                                if "sent" in push_results
                                else "; ".join(dict.fromkeys(push_results))
                            )
                        if send_sms:
                            if not receiver.phone_number:
                                sms_status = "no phone number"
                            elif defer_outlets:
                                sms_status = "queued"
                                pending_outlets.append({
                                    "type": "sms",
                                    "delivery": deliveries[-1],
                                    "phone_number": receiver.phone_number,
                                    "message": received_text,
                                })
                            else:
                                try:
                                    sms_message_sid = send_twilio_sms(receiver.phone_number, received_text)
                                    sms_status = "accepted by gateway"
                                except OutletError as error:
                                    sms_status = str(error)
                    else:
                        sms_status = "not sent: simulated delivery failed"
                        push_status = "not sent: simulated delivery failed"
                    deliveries[-1]["sms_status"] = sms_status
                    deliveries[-1]["sms_message_sid"] = sms_message_sid
                    deliveries[-1]["push_status"] = push_status
                except ConnectionError:
                    deliveries.append({
                        "receiver_id": receiver.device_id,
                        "status": "no route",
                        "acknowledged": False,
                        "attempts": 0,
                        "retries": 0,
                        "native_language": receiver.native_language,
                        "native_language_name": SUPPORTED_LANGUAGES[receiver.native_language],
                        "push_status": "not sent: no route",
                    })
            report = {
                "sender_id": sender_id,
                "payload_bytes": payload_bytes,
                "alert_message": alert_message,
                "failure_rate": failure_rate,
                "max_retries": max_retries,
                "deliveries": deliveries,
            }
            if defer_outlets:
                report["_pending_outlets"] = pending_outlets
            return report

        raise ValueError("Unknown API endpoint")

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.log_date_time_string()} {format % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), SimulatorHandler)
    certificate = os.environ.get("SSL_CERTFILE")
    private_key = os.environ.get("SSL_KEYFILE")
    if bool(certificate) != bool(private_key):
        raise RuntimeError("Set both SSL_CERTFILE and SSL_KEYFILE to enable HTTPS")
    scheme = "http"
    if certificate and private_key:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(certificate, private_key)
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"Relay Lab running at {scheme}://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Relay Lab")
    finally:
        server.server_close()