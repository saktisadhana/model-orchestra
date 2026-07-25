"""Interactive, secret-safe setup for model-orchestra and its Zed profile."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import stat
from pathlib import Path
from typing import Callable

import configure_zed_profile

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
GATEWAY_KEYS = (
    ("C_LITE_1_API_KEY", "c-lite-1 (default host)", True),
    ("C_LITE_2_API_KEY", "c-lite-2 (host fallback)", False),
    ("C_PRO_API_KEY", "c-pro (final host fallback)", False),
)
WORKER_KEYS = (
    ("OPENROUTER_API_KEY", "OpenRouter workers"),
    ("NVIDIA_API_KEY", "NVIDIA workers"),
    ("OPENCODE_API_KEY", "OpenCode Go workers"),
    ("GROQ_API_KEY", "Groq workers"),
    ("SAMBANOVA_API_KEY", "SambaNova workers"),
)
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SetupError(RuntimeError):
    """A recoverable setup problem that should be shown without a traceback."""


def _env_names(text: str) -> set[str]:
    """Return names already present without reading or returning their values."""
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if _ENV_NAME.fullmatch(name):
            names.add(name)
    return names


def _upsert_env(text: str, updates: dict[str, str]) -> str:
    """Update selected dotenv assignments while preserving other lines."""
    lines = text.splitlines()
    seen: set[str] = set()
    rendered: list[str] = []
    for line in lines:
        match = re.match(r"^(\s*)([A-Z][A-Z0-9_]*)(\s*=).*$", line)
        if not match or match.group(2) not in updates:
            rendered.append(line)
            continue
        name = match.group(2)
        seen.add(name)
        rendered.append(f"{name}={updates[name]}")

    for name, value in updates.items():
        if name not in seen:
            if rendered and rendered[-1] != "":
                rendered.append("")
            rendered.append(f"{name}={value}")

    return "\n".join(rendered).rstrip() + "\n"


def _write_env(updates: dict[str, str]) -> None:
    existing = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    ENV_PATH.write_text(_upsert_env(existing, updates), encoding="utf-8")
    try:
        ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _prompt_secret(
    label: str,
    env_name: str,
    existing_names: set[str],
    input_fn: Callable[[str], str] = getpass.getpass,
) -> str | None:
    state = "configured; press Enter to keep" if env_name in existing_names else "optional"
    value = input_fn(f"{label} [{env_name}; {state}]: ")
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise SetupError(f"{env_name} contains an invalid control character.")
    return value or None


def collect_credentials(
    *,
    include_workers: bool,
    input_fn: Callable[[str], str] = getpass.getpass,
) -> dict[str, str]:
    existing_names = _env_names(ENV_PATH.read_text(encoding="utf-8")) if ENV_PATH.exists() else set()
    existing_names.update(name for name, _, _ in GATEWAY_KEYS if os.environ.get(name))
    existing_names.update(name for name, _ in WORKER_KEYS if os.environ.get(name))
    updates: dict[str, str] = {}
    print("Enter replacement keys locally. Input is hidden; values are never displayed.")
    for env_name, label, required in GATEWAY_KEYS:
        value = _prompt_secret(label, env_name, existing_names, input_fn)
        if required and value is None and env_name not in existing_names:
            raise SetupError(f"{env_name} is required for the default gateway host.")
        if value is not None:
            updates[env_name] = value

    if include_workers:
        print("Optional worker providers; press Enter to skip or keep an existing value.")
        for env_name, label in WORKER_KEYS:
            value = _prompt_secret(label, env_name, existing_names, input_fn)
            if value is not None:
                updates[env_name] = value
    return updates


def configured_names() -> set[str]:
    """Return configured variable names, never credential values."""
    names = _env_names(ENV_PATH.read_text(encoding="utf-8")) if ENV_PATH.exists() else set()
    names.update(name for name, _, _ in GATEWAY_KEYS if os.environ.get(name))
    names.update(name for name, _ in WORKER_KEYS if os.environ.get(name))
    return names


def run(
    *,
    include_workers: bool,
    install_profile: bool,
    install_auto_router: bool = False,
    input_fn=getpass.getpass,
) -> None:
    updates = collect_credentials(include_workers=include_workers, input_fn=input_fn)
    if updates:
        _write_env(updates)
        print(f"Saved {len(updates)} credential assignment(s) to the ignored .env file.")
    else:
        print("No credential values changed.")

    if install_profile:
        changed = configure_zed_profile.install(
            configure_zed_profile.default_settings_path(),
            backup=True,
            install_auto_router=install_auto_router,
        )
        print("Zed profile: " + ("updated" if changed else "already current"))

    names = configured_names()
    gateway_ready = [name for name, _, _ in GATEWAY_KEYS if name in names]
    print(f"MCP gateway keys configured: {len(gateway_ready)}/{len(GATEWAY_KEYS)}")
    print("MCP key order: c-lite-1, c-lite-2, then c-pro")
    print("Default model: gpt-5.6-terra; security route: gpt-5.6-sol")
    if len(gateway_ready) < len(GATEWAY_KEYS):
        print("Add missing gateway keys later by rerunning this command.")
    print("Zed host credentials are separate: add each provider key when Zed prompts.")
    print("Zed host fallback is manual: select c-lite-2 or c-pro if c-lite-1 fails.")
    if install_auto_router:
        print("Next: restart Zed, select Model Orchestra Auto, and start a new Agent thread.")
    else:
        print("Next: restart Zed, select Model Orchestra, and start a new Agent thread.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gateway-only",
        action="store_true",
        help="prompt only for c-lite-1, c-lite-2, and c-pro",
    )
    parser.add_argument(
        "--no-profile",
        action="store_true",
        help="write credentials but do not modify Zed settings",
    )
    parser.add_argument(
        "--install-auto-router",
        action="store_true",
        help="also register the Model Orchestra Auto ACP external agent",
    )
    args = parser.parse_args()
    try:
        run(
            include_workers=not args.gateway_only,
            install_profile=not args.no_profile,
            install_auto_router=args.install_auto_router and not args.no_profile,
        )
    except (OSError, SetupError, ValueError) as error:
        print(f"Setup failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
