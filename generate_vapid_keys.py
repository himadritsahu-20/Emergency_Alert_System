"""Create local VAPID keys and append their settings to the root .env file."""

import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
PRIVATE_KEY_FILE = ROOT / "vapid_private.pem"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subject",
        required=True,
        help="VAPID contact URI, for example mailto:you@example.com",
    )
    args = parser.parse_args()
    if not (args.subject.startswith("mailto:") or args.subject.startswith("https://")):
        parser.error("--subject must start with mailto: or https://")

    if PRIVATE_KEY_FILE.exists():
        parser.error("vapid_private.pem already exists; refusing to overwrite it")

    existing_names = set()
    if ENV_FILE.exists():
        existing_names = {
            line.partition("=")[0].strip()
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        }
    if {"VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_SUBJECT"} & existing_names:
        parser.error("VAPID settings already exist in .env; refusing to append duplicates")

    vapid = Vapid()
    vapid.generate_keys()
    vapid.save_key(str(PRIVATE_KEY_FILE))
    PRIVATE_KEY_FILE.chmod(0o600)
    public_bytes = vapid.public_key.public_bytes(
        Encoding.X962,
        PublicFormat.UncompressedPoint,
    )
    public_key = base64.urlsafe_b64encode(public_bytes).decode("ascii").rstrip("=")

    with ENV_FILE.open("a", encoding="utf-8") as env_file:
        if ENV_FILE.stat().st_size:
            env_file.write("\n")
        env_file.write(f"VAPID_PRIVATE_KEY={PRIVATE_KEY_FILE}\n")
        env_file.write(f"VAPID_PUBLIC_KEY={public_key}\n")
        env_file.write(f"VAPID_SUBJECT={args.subject}\n")
    print("VAPID keys generated and settings appended to .env. Private key was not displayed.")


if __name__ == "__main__":
    main()
