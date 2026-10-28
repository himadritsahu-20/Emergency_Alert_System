"""Small model for simulating low-bandwidth alert delivery."""

from dataclasses import dataclass, field
from math import hypot, isfinite
from random import random
import re
from typing import Callable, Mapping

from languages import SUPPORTED_LANGUAGES


@dataclass(frozen=True)
class BandwidthProfile:
    """A triangular bandwidth distribution, in kilobits per second."""

    minimum_kbps: float
    typical_kbps: float
    maximum_kbps: float

    def __post_init__(self) -> None:
        if not 0 < self.minimum_kbps <= self.typical_kbps <= self.maximum_kbps:
            raise ValueError("Bandwidth values must satisfy 0 < minimum <= typical <= maximum")

    @property
    def mean_kbps(self) -> float:
        return (self.minimum_kbps + self.typical_kbps + self.maximum_kbps) / 3


DEFAULT_BANDWIDTH_PROFILES = {
    "bluetooth": BandwidthProfile(250, 1000, 3000),
    "2g": BandwidthProfile(10, 40, 100),
    "3g": BandwidthProfile(100, 1000, 7000),
}


@dataclass(frozen=True)
class Device:
    device_id: str
    x: float
    y: float
    bluetooth_range_m: float = 15
    bluetooth_enabled: bool = True
    cellular_generations: frozenset[str] = frozenset({"2g", "3g"})
    native_language: str = "hi"
    phone_number: str = ""

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        if not isfinite(self.x) or not isfinite(self.y):
            raise ValueError("Device coordinates must be finite")
        if not isfinite(self.bluetooth_range_m) or self.bluetooth_range_m < 0:
            raise ValueError("bluetooth_range_m must be finite and non-negative")
        if not self.cellular_generations <= {"2g", "3g"}:
            raise ValueError("cellular_generations may contain only '2g' and '3g'")
        if self.native_language not in SUPPORTED_LANGUAGES:
            raise ValueError("native_language must be one of the supported Indian languages")
        if self.phone_number and not re.fullmatch(r"\+[1-9]\d{7,14}", self.phone_number):
            raise ValueError("phone_number must use international E.164 format")

    @property
    def coordinates(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass(frozen=True)
class Transmission:
    sender_id: str
    receiver_id: str
    channel: str
    distance_m: float
    estimated_seconds: float


@dataclass(frozen=True)
class DeliveryOutcome:
    status: str
    acknowledged: bool
    attempts: int
    retries: int


def simulate_delivery(
    failure_rate: float,
    max_retries: int,
    random_value: Callable[[], float] = random,
) -> DeliveryOutcome:
    if not isfinite(failure_rate) or not 0 <= failure_rate <= 1:
        raise ValueError("failure_rate must be between 0 and 1")
    if not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError("max_retries must be a non-negative integer")

    for attempt in range(1, max_retries + 2):
        if random_value() >= failure_rate:
            return DeliveryOutcome("acknowledged", True, attempt, attempt - 1)
    return DeliveryOutcome("failed", False, max_retries + 1, max_retries)


@dataclass
class Map:
    width_m: float
    height_m: float
    devices: list[Device] = field(default_factory=list)
    available_cellular_generations: frozenset[str] = frozenset({"2g", "3g"})
    bandwidth_profiles: Mapping[str, BandwidthProfile] = field(
        default_factory=lambda: DEFAULT_BANDWIDTH_PROFILES.copy()
    )

    def __post_init__(self) -> None:
        if not isfinite(self.width_m) or not isfinite(self.height_m):
            raise ValueError("Map dimensions must be finite")
        if self.width_m <= 0 or self.height_m <= 0:
            raise ValueError("Map dimensions must be positive")
        existing_devices = list(self.devices)
        self.devices.clear()
        for device in existing_devices:
            self.add_device(device)

    def add_device(self, device: Device) -> None:
        if not (0 <= device.x <= self.width_m and 0 <= device.y <= self.height_m):
            raise ValueError(f"Device {device.device_id!r} is outside the map bounds")
        if any(existing.device_id == device.device_id for existing in self.devices):
            raise ValueError(f"Device id {device.device_id!r} is already on the map")
        self.devices.append(device)

    def send(self, sender: Device, receiver: Device, payload_bytes: int) -> Transmission:
        if sender not in self.devices or receiver not in self.devices:
            raise ValueError("Both devices must be on this map")
        if sender == receiver:
            raise ValueError("Sender and receiver must be different devices")
        if not isinstance(payload_bytes, int) or payload_bytes < 0:
            raise ValueError("payload_bytes must be a non-negative integer")

        distance_m = hypot(sender.x - receiver.x, sender.y - receiver.y)
        channel = self._select_channel(sender, receiver, distance_m)
        profile = self.bandwidth_profiles[channel]
        estimated_seconds = payload_bytes * 8 / (profile.mean_kbps * 1000)
        return Transmission(sender.device_id, receiver.device_id, channel, distance_m, estimated_seconds)

    def _select_channel(self, sender: Device, receiver: Device, distance_m: float) -> str:
        if sender.bluetooth_enabled and receiver.bluetooth_enabled:
            bluetooth_limit = min(sender.bluetooth_range_m, receiver.bluetooth_range_m)
            if distance_m <= bluetooth_limit:
                return "bluetooth"

        for generation in ("2g", "3g"):
            if (
                generation in self.available_cellular_generations
                and generation in sender.cellular_generations
                and generation in receiver.cellular_generations
            ):
                return generation

        raise ConnectionError("No shared Bluetooth or cellular route is available")