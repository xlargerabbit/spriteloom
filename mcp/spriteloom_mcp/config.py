"""Resolve local installation and MCP artifact paths."""

import json
import os
from pathlib import Path


def project_root() -> Path:
    override = os.environ.get("SPRITELOOM_ROOT")
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2]
    if not (root / "server" / "main.py").is_file():
        raise RuntimeError(
            "Spriteloom installation not found; set SPRITELOOM_ROOT to its folder"
        )
    return root


def user_dir() -> Path:
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home()) / "Spriteloom"


def port() -> int:
    try:
        value = json.loads((user_dir() / "config.json").read_text("utf-8"))["port"]
        if type(value) is int and 1024 <= value <= 65535:
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return 8765


def asset_dir() -> Path:
    override = os.environ.get("SPRITELOOM_MCP_ASSETS")
    return Path(override).expanduser().resolve() if override else user_dir() / "mcp-assets"


def autostart() -> bool:
    return os.environ.get("SPRITELOOM_MCP_AUTOSTART", "1").lower() not in (
        "0", "false", "no"
    )
