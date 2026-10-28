"""Minimal root .env loading without a third-party dependency."""

import os
from pathlib import Path


def load_root_env(env_path: Path | None = None) -> None:
    env_path = env_path or Path(__file__).resolve().parent / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return

    for line in lines:
        assignment = line.strip()
        if not assignment or assignment.startswith("#"):
            continue
        if assignment.startswith("export "):
            assignment = assignment[7:].lstrip()
        name, separator, value = assignment.partition("=")
        name = name.strip()
        if (
            not separator
            or not name.isidentifier()
            or (name != "GROQ_API_KEY" and os.environ.get(name))
        ):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ[name] = value