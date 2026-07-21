"""Install the Model Orchestra agent profile in Zed user settings."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROFILE_ID = "model-orchestra"
AUTO_ROUTER_ID = "model-orchestra-auto"
GATEWAY_API_URL = "https://68886868.xyz/v1"
GATEWAY_MODELS = [
    {
        "name": "gpt-5.6-terra",
        "display_name": "GPT-5.6 Terra",
        "max_tokens": 200000,
        "max_output_tokens": 32000,
        "max_completion_tokens": 200000,
        "reasoning_effort": "high",
        "capabilities": {
            "tools": True,
            "images": True,
            "parallel_tool_calls": True,
            "prompt_cache_key": True,
            "chat_completions": True,
            "interleaved_reasoning": False,
            "max_tokens_parameter": True,
        },
    },
    {
        "name": "gpt-5.6-sol",
        "display_name": "GPT-5.6 Sol",
        "max_tokens": 200000,
        "max_output_tokens": 32000,
        "max_completion_tokens": 200000,
        "reasoning_effort": "high",
        "capabilities": {
            "tools": True,
            "images": True,
            "parallel_tool_calls": True,
            "prompt_cache_key": True,
            "chat_completions": True,
            "interleaved_reasoning": False,
            "max_tokens_parameter": True,
        },
    },
]
GATEWAY_PROVIDERS = {
    provider: {"api_url": GATEWAY_API_URL, "available_models": GATEWAY_MODELS}
    for provider in ("c-lite-1", "c-lite-2", "c-pro")
}
PROFILE = {
    "name": "Model Orchestra",
    "tools": {
        "copy_path": True,
        "create_directory": True,
        "delete_path": True,
        "diagnostics": True,
        "edit_file": True,
        "fetch": True,
        "find_path": True,
        "grep": True,
        "list_directory": True,
        "move_path": True,
        "read_file": True,
        "terminal": True,
        "write_file": True,
    },
    "enable_all_context_servers": False,
    "context_servers": {
        "model-orchestra": {
            "tools": {
                "auto_delegate": True,
                "batch_delegate": True,
                "compact": True,
                "cost_report": True,
                "delegate": True,
                "delegate_verified": True,
                "list_workers": True,
                "pipeline": True,
                "swarm": True,
            }
        }
    },
    "default_model": {
        "provider": "c-lite-1",
        "model": "gpt-5.6-terra",
    },
}


def default_settings_path() -> Path:
    return Path.home() / "AppData" / "Roaming" / "Zed" / "settings.json"


def split_comment_header(text: str) -> tuple[str, str]:
    object_start = text.find("{")
    if object_start < 0:
        raise ValueError("settings file does not contain a JSON object")
    header = text[:object_start]
    body = text[object_start:]
    return header, body


def auto_router_config() -> dict[str, object]:
    """Return the secret-free Zed custom-agent registration."""
    return {
        "type": "custom",
        "command": sys.executable,
        "args": [str(ROOT / "acp_router.py")],
        "env": {},
    }


def install(settings_path: Path, backup: bool = True, install_auto_router: bool = False) -> bool:
    original = settings_path.read_text(encoding="utf-8") if settings_path.exists() else "{}\n"
    header, body = split_comment_header(original)
    settings = json.loads(body)
    if not isinstance(settings, dict):
        raise ValueError("Zed settings root must be an object")

    agent = settings.setdefault("agent", {})
    if not isinstance(agent, dict):
        raise ValueError("agent setting must be an object")
    profiles = agent.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("agent.profiles must be an object")

    language_models = settings.setdefault("language_models", {})
    if not isinstance(language_models, dict):
        raise ValueError("language_models setting must be an object")
    providers = language_models.setdefault("openai_compatible", {})
    if not isinstance(providers, dict):
        raise ValueError("language_models.openai_compatible must be an object")

    changed = profiles.get(PROFILE_ID) != PROFILE
    profiles[PROFILE_ID] = PROFILE
    if install_auto_router:
        agent_servers = settings.setdefault("agent_servers", {})
        if not isinstance(agent_servers, dict):
            raise ValueError("agent_servers setting must be an object")
        router = auto_router_config()
        if agent_servers.get(AUTO_ROUTER_ID) != router:
            changed = True
            agent_servers[AUTO_ROUTER_ID] = router
    for provider, config in GATEWAY_PROVIDERS.items():
        if providers.get(provider) != config:
            changed = True
            providers[provider] = config

    if not changed:
        return False

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if backup and settings_path.exists():
        backup_path = settings_path.with_suffix(".json.model-orchestra.bak")
        if not backup_path.exists():
            shutil.copy2(settings_path, backup_path)

    rendered = header + json.dumps(settings, indent=2, ensure_ascii=True) + "\n"
    json.loads(split_comment_header(rendered)[1])
    settings_path.write_text(rendered, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=default_settings_path())
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument(
        "--install-auto-router",
        action="store_true",
        help="also register the Model Orchestra Auto ACP external agent",
    )
    args = parser.parse_args()

    try:
        changed = install(
            args.settings,
            backup=not args.no_backup,
            install_auto_router=args.install_auto_router,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Zed profile installation failed: {error}")
        return 1

    state = "installed" if changed else "already installed"
    print(f"Model Orchestra profile {state}: {args.settings}")
    if args.install_auto_router:
        print("Model Orchestra Auto external agent: installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
