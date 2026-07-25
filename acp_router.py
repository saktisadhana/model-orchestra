"""ACP external agent that auto-selects Terra, K3, or Sol per substantive turn."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import anthropic
from acp import (
    Agent,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    SetSessionConfigOptionResponse,
    run_agent,
    start_tool_call,
    text_block,
    tool_content,
    update_agent_message,
    update_tool_call,
)
from acp.interfaces import Client
from acp.schema import (
    AgentCapabilities,
    ClientCapabilities,
    Implementation,
    PermissionOption,
    PromptCapabilities,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    ToolCallUpdate,
)

import server

MODEL_SETTING = "model"
MODEL_NAMES = {
    "terra": "GPT-5.6 Terra",
    "k3": "Kimi K3",
    "sol": "GPT-5.6 Sol",
}
MAX_TOOL_STEPS = 8
MAX_FILE_CHARS = 120_000
MAX_TERMINAL_OUTPUT = 48_000

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "line": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a UTF-8 text file inside the workspace. Zed asks the user for permission.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_terminal",
        "description": "Run one program in the workspace. Zed asks the user for permission. Use command and args, not shell syntax.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
        },
    },
]

SYSTEM_PROMPT = """You are Model Orchestra Auto, a pragmatic coding agent in Zed.
Use the workspace tools before assuming implementation details. For substantial
non-security repository implementation, work as the Kimi K3 implementation worker;
keep architecture, review, and final acceptance explicit. Tiny local edits may stay
with the host. Security, exploit, cryptography, malware, and forensics work belongs
on Sol. Keep changes focused, validate the changed surface, and report concrete
results. Auto routing is recomputed for each substantive turn. A transition to a
new model starts a fresh model context so provider-specific thinking and tool blocks
are never replayed across incompatible models. An explicitly selected model remains
pinned after work begins. Use Zed-backed tools for all workspace and terminal
operations. Do not expose credentials or read files outside the workspace. Request
only the tools needed to complete the task."""


@dataclass
class SessionState:
    cwd: Path
    model_setting: str = "auto"
    resolved_model: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    current_task: asyncio.Task[Any] | None = None
    turns_started: int = 0
    current_returned_chars: int = 0


def route_auto(prompt: str) -> str:
    """Choose Sol for security, K3 for repository work, otherwise Terra."""
    if server._is_security(prompt):
        return "sol"
    return "k3" if server._task_kind(prompt) == "repository" else "terra"


def _workspace_path(workspace: Path, requested: str) -> str:
    if not isinstance(requested, str) or not requested.strip():
        raise ValueError("path must be a non-empty workspace-relative path")
    root = workspace.resolve()
    candidate = Path(requested)
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("path must remain inside the workspace") from error
    return str(target)


def _config_options(state: SessionState) -> list[SessionConfigOptionSelect]:
    return [
        SessionConfigOptionSelect(
            id=MODEL_SETTING,
            name="Model",
            description="Auto routes every substantive turn; explicit selections pin after work starts.",
            category="model",
            type="select",
            currentValue=state.model_setting,
            options=[
                SessionConfigSelectOption(value="auto", name="Auto"),
                SessionConfigSelectOption(value="terra", name=MODEL_NAMES["terra"]),
                SessionConfigSelectOption(value="k3", name=MODEL_NAMES["k3"]),
                SessionConfigSelectOption(value="sol", name=MODEL_NAMES["sol"]),
            ],
        )
    ]


def _prompt_text(blocks: list[Any]) -> str:
    parts: list[str] = []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(part for part in parts if part.strip()).strip()


def _message_content(message: Any) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for block in message.content:
        # Thinking blocks MUST be kept. terra, sol, and k27 all emit them, and the
        # API rejects a continued tool-use loop whose assistant turn dropped the
        # thinking block that preceded the tool_use.
        if hasattr(block, "model_dump"):
            content.append(block.model_dump(by_alias=True, exclude_none=True))
        elif isinstance(block, dict):
            content.append(block)
    return content


class ModelOrchestraAuto(Agent):
    def __init__(self) -> None:
        self._conn: Client | None = None
        self._sessions: dict[str, SessionState] = {}

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **_: Any,
    ) -> InitializeResponse:
        return InitializeResponse(
            protocolVersion=protocol_version,
            agentCapabilities=AgentCapabilities(
                promptCapabilities=PromptCapabilities(image=False, audio=False),
            ),
            agentInfo=Implementation(
                name="model-orchestra-auto",
                title="Model Orchestra Auto",
                version="0.1.0",
            ),
        )

    async def new_session(self, cwd: str, **_: Any) -> NewSessionResponse:
        session_id = uuid4().hex
        state = SessionState(cwd=Path(cwd).resolve())
        self._sessions[session_id] = state
        return NewSessionResponse(sessionId=session_id, configOptions=_config_options(state))

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **_: Any
    ) -> SetSessionConfigOptionResponse | None:
        state = self._session(session_id)
        if config_id != MODEL_SETTING or value not in {"auto", "terra", "k3", "sol"}:
            return None
        selected = str(value)
        if state.turns_started and selected != state.model_setting:
            raise ValueError("the model setting is locked after the first substantive prompt")
        state.model_setting = selected
        state.resolved_model = selected if selected in MODEL_NAMES else None
        return SetSessionConfigOptionResponse(configOptions=_config_options(state))

    async def prompt(self, session_id: str, prompt: list[Any], **_: Any) -> PromptResponse:
        state = self._session(session_id)
        prompt_text = _prompt_text(prompt)
        if not prompt_text:
            await self._send_text(session_id, "Please provide a text prompt.")
            return PromptResponse(stopReason="end_turn")
        next_model = (
            state.model_setting
            if state.model_setting != "auto"
            else route_auto(prompt_text)
        )
        if (
            state.model_setting == "auto"
            and state.resolved_model is not None
            and state.resolved_model != next_model
        ):
            state.messages.clear()
        state.resolved_model = next_model
        state.messages.append({"role": "user", "content": prompt_text})
        state.current_task = asyncio.current_task()
        state.turns_started += 1
        state.current_returned_chars = 0
        operation = {"tool_steps": 0, "parent": server._ORCHESTRATION_COLLECTOR.get()}
        operation_token = server._ORCHESTRATION_COLLECTOR.set(operation)
        started = time.monotonic()
        route = {"terra": None, "k3": "repository-edit", "sol": "security"}[next_model]
        try:
            response = await self._run_turn(session_id, state)
            server._track_orchestration(
                "acp_auto", route, [next_model], "success", None,
                time.monotonic() - started, operation.get("tool_steps", 0),
                state.current_returned_chars,
            )
            return response
        except asyncio.CancelledError:
            server._track_orchestration(
                "acp_auto", route, [next_model], "cancelled", "host_cancelled",
                time.monotonic() - started, operation.get("tool_steps", 0),
                state.current_returned_chars,
            )
            return PromptResponse(stopReason="cancelled")
        except Exception as error:
            diagnostic = f"Model Orchestra error: {server._safe_error(error)}"
            await self._send_text(session_id, diagnostic)
            server._track_orchestration(
                "acp_auto", route, [next_model], "infrastructure_failure",
                "worker_error", time.monotonic() - started,
                operation.get("tool_steps", 0), state.current_returned_chars,
            )
            return PromptResponse(stopReason="end_turn")
        finally:
            server._ORCHESTRATION_COLLECTOR.reset(operation_token)
            state.current_task = None

    async def cancel(self, session_id: str, **_: Any) -> None:
        state = self._sessions.get(session_id)
        if state and state.current_task:
            state.current_task.cancel()

    def _session(self, session_id: str) -> SessionState:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise ValueError("unknown ACP session") from error

    async def _run_turn(self, session_id: str, state: SessionState) -> PromptResponse:
        for _ in range(MAX_TOOL_STEPS + 1):
            message = await self._create_message(session_id, state)
            state.messages.append({"role": "assistant", "content": _message_content(message)})
            tool_uses = [block for block in message.content if getattr(block, "type", None) == "tool_use"]
            if not tool_uses:
                return PromptResponse(stopReason="max_tokens" if message.stop_reason == "max_tokens" else "end_turn")
            if len(state.messages) > MAX_TOOL_STEPS * 2 + 2:
                await self._send_text(session_id, "Stopped after the configured tool-call limit.")
                return PromptResponse(stopReason="max_turn_requests")
            results = []
            for tool_use in tool_uses:
                result = await self._execute_tool(session_id, state, tool_use)
                results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": result})
            state.messages.append({"role": "user", "content": results})
        await self._send_text(session_id, "Stopped after the configured tool-call limit.")
        return PromptResponse(stopReason="max_turn_requests")

    async def _create_message(self, session_id: str, state: SessionState) -> Any:
        selected_model = state.resolved_model or "terra"
        provider, model_id = server.resolve(selected_model)
        estimated_input = sum(len(str(message.get("content", ""))) for message in state.messages) // 4
        server._budget_check(selected_model, estimated_input, server.AGENT_MAX_TOKENS)
        keys = server._keys_for(provider)
        if not keys:
            names = server.PROVIDERS[provider].get("api_key_envs") or [server.PROVIDERS[provider]["api_key_env"]]
            raise RuntimeError("missing gateway credential: configure one of " + ", ".join(names))
        last_error: Exception | None = None
        retries = 3
        deadline = time.monotonic() + server.CASCADE_DEADLINE
        for attempt in range(retries):
            for key_index, key in enumerate(keys):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise last_error or TimeoutError("ACP cascade deadline exceeded")
                client = anthropic.AsyncAnthropic(
                    base_url=server.PROVIDERS[provider]["base_url"], api_key=key
                )
                try:
                    server._record_event(
                        "provider_attempt", provider=provider,
                        key_index=key_index, attempt=attempt + 1,
                    )
                    async with client.messages.stream(
                        model=model_id,
                        max_tokens=server.AGENT_MAX_TOKENS,
                        temperature=0.2,
                        system=server._cacheable_system(SYSTEM_PROMPT),
                        messages=state.messages,
                        tools=TOOLS,
                        timeout=min(server.REQUEST_TIMEOUT, remaining),
                    ) as stream:
                        async for text in stream.text_stream:
                            await self._send_text(session_id, text)
                        message = await stream.get_final_message()
                        server._track(
                            selected_model,
                            server.AnthropicUsage(
                                message.usage.input_tokens,
                                message.usage.output_tokens,
                                int(getattr(message.usage, "cache_read_input_tokens", 0) or 0),
                                int(getattr(message.usage, "cache_creation_input_tokens", 0) or 0),
                            ),
                        )
                        return message
                except Exception as error:
                    last_error = error
                    if server._context_overflow(error):
                        raise
                    if not server._key_exhausted(error) and not server._is_transient(error):
                        raise
                    if server._key_exhausted(error):
                        if key_index < len(keys) - 1:
                            server._record_event(
                                "key_rotation", provider=provider,
                                from_key_index=key_index,
                                to_key_index=key_index + 1,
                            )
                        continue
                finally:
                    await client.close()
            if last_error is not None and server._is_transient(last_error) and attempt < retries - 1:
                pause = 0.5 * (2 ** attempt)
                if pause >= deadline - time.monotonic():
                    raise last_error
                server._record_event(
                    "provider_retry", provider=provider, attempt=attempt + 1
                )
                await asyncio.sleep(pause)
                continue
            break
        assert last_error is not None
        raise last_error

    async def _execute_tool(self, session_id: str, state: SessionState, tool_use: Any) -> str:
        name = tool_use.name
        server._record_agent_tool_step(state.resolved_model or "terra", name)
        tool_id = f"tool-{tool_use.id}"
        kind = {"read_file": "read", "write_file": "edit", "run_terminal": "execute"}.get(name, "other")
        await self._update_tool(session_id, start_tool_call(tool_id, name, kind=kind, status="in_progress", raw_input=tool_use.input))
        try:
            if name == "read_file":
                result = await self._read_file(session_id, state, tool_use.input)
            elif name == "write_file":
                result = await self._write_file(session_id, state, tool_id, tool_use.input)
            elif name == "run_terminal":
                result = await self._run_terminal(session_id, state, tool_id, tool_use.input)
            else:
                result = f"Unsupported tool: {name}"
            await self._update_tool(session_id, update_tool_call(tool_id, status="completed", content=[tool_content(text_block(result[:MAX_TERMINAL_OUTPUT]))]))
            return result
        except Exception as error:
            result = f"Tool error: {server._safe_error(error)}"
            await self._update_tool(session_id, update_tool_call(tool_id, status="failed", content=[tool_content(text_block(result))]))
            return result

    async def _read_file(self, session_id: str, state: SessionState, data: dict[str, Any]) -> str:
        conn = self._connection()
        path = _workspace_path(state.cwd, data.get("path", ""))
        response = await conn.read_text_file(session_id, path, line=data.get("line"), limit=data.get("limit"))
        return response.content[:MAX_FILE_CHARS]

    async def _write_file(self, session_id: str, state: SessionState, tool_id: str, data: dict[str, Any]) -> str:
        path = _workspace_path(state.cwd, data.get("path", ""))
        content = data.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be text")
        if len(content) > MAX_FILE_CHARS:
            raise ValueError(f"content exceeds {MAX_FILE_CHARS:,} characters")
        await self._require_permission(session_id, tool_id, "Write file")
        await self._connection().write_text_file(session_id, path, content)
        return f"Wrote {path}"

    async def _run_terminal(self, session_id: str, state: SessionState, tool_id: str, data: dict[str, Any]) -> str:
        command = data.get("command")
        args = data.get("args", [])
        if not isinstance(command, str) or not command.strip() or not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError("command must be text and args must be a list of text")
        await self._require_permission(session_id, tool_id, "Run terminal command")
        conn = self._connection()
        terminal = await conn.create_terminal(session_id, command, args=args, cwd=str(state.cwd), output_byte_limit=MAX_TERMINAL_OUTPUT)
        try:
            await conn.wait_for_terminal_exit(session_id, terminal.terminal_id)
            output = await conn.terminal_output(session_id, terminal.terminal_id)
            return output.output[:MAX_TERMINAL_OUTPUT]
        finally:
            await conn.release_terminal(session_id, terminal.terminal_id)

    async def _require_permission(self, session_id: str, tool_id: str, label: str) -> None:
        response = await self._connection().request_permission(
            session_id,
            ToolCallUpdate(toolCallId=tool_id, title=label),
            [
                PermissionOption(optionId="allow_once", name="Allow once", kind="allow_once"),
                PermissionOption(optionId="reject_once", name="Reject", kind="reject_once"),
            ],
        )
        if getattr(response.outcome, "option_id", "") != "allow_once":
            raise PermissionError("permission denied")

    async def _send_text(self, session_id: str, text: str) -> None:
        if text:
            state = self._sessions.get(session_id)
            if state is not None:
                state.current_returned_chars += len(text)
            await self._connection().session_update(session_id, update_agent_message(text_block(text)))

    async def _update_tool(self, session_id: str, update: Any) -> None:
        await self._connection().session_update(session_id, update)

    def _connection(self) -> Client:
        if self._conn is None:
            raise RuntimeError("ACP client is not connected")
        return self._conn


if __name__ == "__main__":
    asyncio.run(run_agent(ModelOrchestraAuto()))
