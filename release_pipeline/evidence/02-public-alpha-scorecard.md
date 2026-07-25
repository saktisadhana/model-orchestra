# Phase 02 Public Alpha Scorecard

## Identity

- Phase: `02-public-alpha`
- Decision date: `2026-07-25`
- Candidate version: `0.2.0a1`
- Decision: `GO` to private alpha/user trial; public publication remains `PAUSE` until the first remote CI matrix succeeds
- Worker route: K3 attempted once through Model Orchestra; provider returned 503 `model_not_found` before workspace tool execution
- Fallback: host-local implementation once, per repository policy

## Spend

| Category | Actual | Notes |
|---|---:|---|
| Paid evaluation | `0` | No live provider benchmark or delegated user task |
| K3 implementation | uncertain pending liability | Provider failed before tool execution; Phase 01 accounting retained the uncertain attempt |
| Host implementation | existing session | Not a provider invoice |

## Deliverables

- `pyproject.toml`, wheel, sdist, and `model-orchestra` console entry point.
- Compatibility package `model_orchestra/`; root `server.py` remains supported.
- CLI: `serve`, `check`, `doctor`, `tools`, `migrate-config`, and guarded `benchmark --live`.
- Versioned neutral configuration example and JSON schema.
- Explicit config resolution: CLI path, `MODEL_ORCHESTRA_CONFIG`, then per-user path.
- User state resolves beside the selected config, not inside site-packages.
- Eight stable public tools; experimental tools are removed from the alpha registry.
- Frozen v1 tool-schema snapshot with deterministic drift check.
- Canonical offline release check and dependency-free MCP stdio smoke.
- Reproducible release builder and normalized sdist.
- Windows/Linux GitHub Actions matrix for Python 3.11 and 3.12.
- Public install, host registration, live-test guard, migration, and uninstall documentation.

## Artifact Evidence

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `model_orchestra-0.2.0a1-py3-none-any.whl` | `55,794` | `d8fe79c751d774837f6f54130d8fd2309c3b8dbb35af13660fcda505d307c48f` |
| `model_orchestra-0.2.0a1.tar.gz` | `51,027` | `7d69328eeae5439a997012ed3fc9135b9654b745186d35b50c5f98294becc040` |

Reproducibility is verified per artifact, not asserted jointly.

- The sdist reproduces exactly. `tools/normalize_sdist.py` rewrites the tar at the
  fixed release epoch, so its hash is independent of the builder.
- The wheel did **not** originally reproduce. An earlier recorded build produced
  `55,797` bytes / `5be9eb44228ef594462263da07f0454173b3824dbd98f417c0d7e5976730bf58`,
  while rebuilding produced `55,794` bytes /
  `33a121e4f4bd477d633922f125a6d61a4317f9679b3238db0fc7154cd1547644` on both
  Python 3.11 and 3.13. Every zip member matched in name, uncompressed length,
  CRC-32, and stored timestamp; only the ZIP container bytes differed, because the
  compressed stream tracked the builder's zlib and setuptools versions.
- Remediation: `tools/normalize_wheel.py` now rewrites the wheel with a fixed
  epoch, `compresslevel=9`, `create_system=3`, deterministic `external_attr`, and
  sorted members with `RECORD` last. `tools/build_release.py` runs both
  normalizers and honours both return codes.
- Re-verified after remediation: two builds from different interpreters
  (CPython 3.13 and 3.11) produced byte-identical wheel and sdist hashes, and the
  normalized wheel installs, imports, validates config, and serves MCP cleanly.

The `5be9eb44…` wheel is superseded. Regenerate release artifacts with
`python tools/build_release.py` before publication; `dist/` may still hold the
older non-reproducible wheel.

Archive scans found no private gateway endpoint, `C_LITE`/`C_PRO` alias, private machine project path, private `config.json`, `.mcp.json`, tests, or generated reports.

## Verification

- Canonical offline pytest: `95 passed, 3 deselected`.
- `python tools/check.py`: pass.
- Routing benchmark: `12/12`.
- Alias/failover invariants: all 19 aliases plus passthrough and invalid-input cases pass.
- Offline usefulness diagnostic: `5.3/10` on a clean checkout; `7.2/10` in a
  working tree that still holds locally generated `docs/REPORT.json` and
  `docs/USEFULNESS_BENCHMARK.json`. Those reports are deliberately not
  committed, so `5.3/10` is the reproducible number and the one CI will show.
  Synthetic context reduction `95.5%` in both cases.
- Public tool schema snapshot: pass.
- `git diff --check`: pass; line-ending warnings only.
- Final wheel: isolated Windows install, config validation, diagnostics, eight-tool discovery, protocol calls, clean shutdown, and uninstall pass.
- Final normalized sdist: isolated Windows install, config/tool smoke pass.
- WSL2 Linux Python 3.13: wheel and normalized sdist install, config/tool smoke, live guard, and uninstall pass.
- Dependency-free stdio smoke against the installed wheel, the installed sdist,
  and the normalized wheel: `initialize`, `notifications/initialized`,
  `tools/list`, `stable_tool_surface`, `list_workers`, `route_preview`, and
  clean shutdown all pass, with 8/8 stable tools discovered.
- Hermes, Claude Code, and MCP Inspector were observed connecting during
  development, but no captured output or rerun procedure was archived. They are
  recorded as `NOT RECORDED`, not `PASS`. See
  [02-host-smoke.md](02-host-smoke.md).

## Exit Gate

| Gate | Result |
|---|---|
| Earlier deterministic gates remain green | PASS |
| Reproducible wheel and sdist | PASS |
| Install and uninstall on Windows | PASS |
| Install and uninstall on executed Linux environment | PASS (WSL2) |
| Neutral examples and errors | PASS |
| Reproducible wheel (remediated) | PASS |
| Three client smoke tests | FAIL; only the repository stdio client is recorded |
| Missing-credential failure actionable and redacted | PASS |
| Alpha tool schemas snapshotted | PASS |
| Remote Windows/Linux Python 3.11/3.12 CI | PENDING first pushed run |
| Fresh live delegated user task | NOT RUN; requires explicit paid approval |

## Decision

Phase 02 implementation and local exit-gate evidence are complete. The artifact is approved for a private alpha/user trial and for opening a release PR.

Do not publish the alpha publicly until the new GitHub Actions matrix succeeds on a pushed branch. Do not claim verified savings: the alpha proves installability, portability, policy enforcement, and host compatibility, not real-workload budget benefit.
