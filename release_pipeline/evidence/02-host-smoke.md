# Phase 02 Host and Client Smoke Evidence

## Purpose

Record repeatable client evidence for the v0.2.0a1 alpha. A protocol-compatible
host is not a support claim. Only a recorded, rerunnable result counts.

## Environment

- Platform: Windows 11, `win32`
- Interpreters: CPython 3.11.15 and CPython 3.13
- Artifacts under test: `model_orchestra-0.2.0a1-py3-none-any.whl` and
  `model_orchestra-0.2.0a1.tar.gz`, built by `python tools/build_release.py`
- Every run used a virtual environment and working directory **outside** the
  checkout, so the source tree cannot shadow the installed package.

## Recorded results

| Client | Transport | Result | Evidence |
|---|---|---|---|
| `tools/smoke_stdio.py` against installed wheel | stdio | PASS | 7/7 steps, 8/8 stable tools, shutdown rc 0 |
| `tools/smoke_stdio.py` against installed sdist | stdio | PASS | 7/7 steps, 8/8 stable tools, shutdown rc 0 |
| `tools/smoke_stdio.py` against normalized wheel | stdio | PASS | install, import, config validation, serve, clean shutdown |
| Hermes | stdio | NOT RECORDED | see below |
| Claude Code | stdio | NOT RECORDED | see below |
| MCP Inspector | stdio | NOT RECORDED | see below |

Steps asserted per PASS row, in order: `initialize`,
`notifications/initialized`, `tools/list`, `stable_tool_surface`,
`tools/call list_workers`, `tools/call route_preview`, clean shutdown with
return code 0.

## Rerun commands

Build and install into a clean environment outside the checkout, then:

```bash
python tools/smoke_stdio.py --config "$CONFIG" --json -- "$VENV_PYTHON" -m model_orchestra serve
```

`$CONFIG` is the packaged neutral example, obtained from the installed package
so no private configuration is involved:

```bash
CONFIG=$("$VENV_PYTHON" -c "from model_orchestra.config import example_config_path; print(example_config_path())")
```

Exit code 0 means every step passed, including the stable-tool-surface check.

## Unrecorded host claims

Hermes, Claude Code, and MCP Inspector were previously reported as connecting
successfully, but no captured output, log, or rerun procedure was archived, so
those results are not reproducible from this repository. They are recorded here
as `NOT RECORDED` rather than `PASS`.

To convert each to recorded evidence, capture the following and append it to
this file:

- **Hermes:** isolated `HERMES_HOME`, the MCP registration used, and the
  discovered tool list.
- **Claude Code:** isolated `CLAUDE_CONFIG_DIR`, the MCP registration used, and
  the discovered tool list.
- **MCP Inspector:** the CLI invocation, its `tools/list` output, and one
  `route_preview` result.

Redact credentials, base URLs of private gateways, and machine-specific paths
before archiving.

## Limitation

Linux coverage is not recorded here. The GitHub Actions matrix in
`.github/workflows/` is the intended source of Linux evidence and has not yet
executed on a pushed branch, because the repository has no commits containing
this work.
