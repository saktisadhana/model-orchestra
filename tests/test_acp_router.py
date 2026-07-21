"""Offline checks for the Model Orchestra ACP external agent."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import acp_router
import configure_zed_profile


def test_auto_route_uses_sol_for_forensics_and_terra_for_general_work() -> None:
    assert acp_router.route_auto("Analyze this Windows memory forensics image") == "sol"
    assert acp_router.route_auto("Implement a small settings panel") == "terra"


def test_workspace_path_rejects_escapes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        assert acp_router._workspace_path(root, "src/main.py") == str(root / "src" / "main.py")
        try:
            acp_router._workspace_path(root, "../outside.txt")
        except ValueError:
            pass
        else:
            raise AssertionError("workspace traversal was accepted")


def test_model_is_pinned_after_selection() -> None:
    async def check() -> None:
        agent = acp_router.ModelOrchestraAuto()
        response = await agent.new_session(str(ROOT))
        session_id = response.session_id
        updated = await agent.set_config_option("model", session_id, "sol")
        assert updated is not None
        assert updated.config_options[0].current_value == "sol"
        assert agent._sessions[session_id].resolved_model == "sol"
        try:
            await agent.set_config_option("model", session_id, "terra")
        except ValueError:
            pass
        else:
            raise AssertionError("pinned model was changed")

    asyncio.run(check())


def test_new_session_exposes_model_selector() -> None:
    async def check() -> None:
        agent = acp_router.ModelOrchestraAuto()
        response = await agent.new_session(str(ROOT))
        option = response.config_options[0]
        assert option.id == "model"
        assert option.category == "model"
        assert option.current_value == "auto"
        assert [item.value for item in option.options] == ["auto", "terra", "sol"]

    asyncio.run(check())


def test_auto_router_registration_is_secret_free_and_idempotent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        settings = Path(directory) / "settings.json"
        assert configure_zed_profile.install(settings, install_auto_router=True)
        assert not configure_zed_profile.install(settings, install_auto_router=True)
        rendered = json.loads(settings.read_text(encoding="utf-8"))
        router = rendered["agent_servers"][configure_zed_profile.AUTO_ROUTER_ID]
        assert router == configure_zed_profile.auto_router_config()
        assert "key" not in json.dumps(router).lower()


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"ok ({len(tests)} tests)")


if __name__ == "__main__":
    main()
