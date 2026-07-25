"""Offline checks for the Model Orchestra ACP external agent."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import acp_router
import configure_zed_profile


def test_auto_route_uses_sol_for_security_k3_for_repository_and_terra_elsewhere() -> None:
    assert acp_router.route_auto("Analyze this Windows memory forensics image") == "sol"
    assert acp_router.route_auto("Implement a small settings panel") == "k3"
    assert acp_router.route_auto("Write a function to sort a list") == "terra"


def test_workspace_path_rejects_escapes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        assert Path(acp_router._workspace_path(root, "src/main.py")) == (root / "src" / "main.py").resolve()
        try:
            acp_router._workspace_path(root, "../outside.txt")
        except ValueError:
            pass
        else:
            raise AssertionError("workspace traversal was accepted")


def test_workspace_path_rejects_symlink_escape_when_supported() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "workspace"
        outside = Path(directory) / "outside"
        root.mkdir()
        outside.mkdir()
        link = root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            return
        try:
            acp_router._workspace_path(root, "link/secret.txt")
        except ValueError:
            pass
        else:
            raise AssertionError("workspace symlink escape was accepted")


def test_model_setting_can_change_before_work_and_pins_after() -> None:
    async def check() -> None:
        agent = acp_router.ModelOrchestraAuto()
        response = await agent.new_session(str(ROOT))
        session_id = response.session_id
        updated = await agent.set_config_option("model", session_id, "sol")
        assert updated is not None
        assert updated.config_options[0].current_value == "sol"
        updated = await agent.set_config_option("model", session_id, "terra")
        assert updated is not None
        assert updated.config_options[0].current_value == "terra"

        async def fake_turn(session_id, state):
            assert session_id
            assert state.resolved_model == "terra"
            return acp_router.PromptResponse(stopReason="end_turn")

        agent._run_turn = fake_turn
        await agent.prompt(
            session_id, [SimpleNamespace(type="text", text="Explain this function")]
        )
        try:
            await agent.set_config_option("model", session_id, "k3")
        except ValueError:
            pass
        else:
            raise AssertionError("model setting changed after work began")

    asyncio.run(check())


def test_auto_routes_each_substantive_turn_and_resets_on_transition() -> None:
    async def check() -> None:
        agent = acp_router.ModelOrchestraAuto()
        response = await agent.new_session(str(ROOT))
        session_id = response.session_id
        seen: list[tuple[str | None, int]] = []

        async def fake_turn(session_id, state):
            assert session_id
            seen.append((state.resolved_model, len(state.messages)))
            return acp_router.PromptResponse(stopReason="end_turn")

        agent._run_turn = fake_turn
        for text in (
            "Explain why this algorithm is stable",
            "Implement a settings panel in the existing project",
            "Analyze this Windows memory forensics image",
        ):
            await agent.prompt(
                session_id, [SimpleNamespace(type="text", text=text)]
            )

        assert [model for model, _ in seen] == ["terra", "k3", "sol"]
        assert [count for _, count in seen] == [1, 1, 1]
        state = agent._sessions[session_id]
        assert state.model_setting == "auto"
        assert state.resolved_model == "sol"

    asyncio.run(check())


def test_new_session_exposes_model_selector() -> None:
    async def check() -> None:
        agent = acp_router.ModelOrchestraAuto()
        response = await agent.new_session(str(ROOT))
        option = response.config_options[0]
        assert option.id == "model"
        assert option.category == "model"
        assert option.current_value == "auto"
        assert [item.value for item in option.options] == ["auto", "terra", "k3", "sol"]
        assert "every substantive turn" in option.description

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


def test_hermes_registration_is_secret_free_and_idempotent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        settings = Path(directory) / "settings.json"
        assert configure_zed_profile.install(settings, install_hermes=True)
        assert not configure_zed_profile.install(settings, install_hermes=True)
        rendered = json.loads(settings.read_text(encoding="utf-8"))
        hermes = rendered["agent_servers"][configure_zed_profile.HERMES_AGENT_ID]
        assert hermes == configure_zed_profile.hermes_agent_config()
        assert Path(hermes["command"]).name == "hermes-acp.exe"
        assert "key" not in json.dumps(hermes).lower()


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
