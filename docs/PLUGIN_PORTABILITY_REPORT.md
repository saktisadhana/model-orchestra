# Claude Code plugins in Zed: portability and cost report

Generated from the local configuration on 2026-07-21. Reproduce the inventory with:

```sh
python plugin_portability.py
```

## Result

- Claude Code plugins enabled: **18**
- Corresponding Zed Agent Skills installed: **18/18**
- Total global Zed Agent Skills available: **61**
- Capability parity: **11 direct, 7 partial, 0 unavailable**
- Skill catalog validation: **61 valid, 0 errors**

This is capability portability, not literal plugin installation. Zed does not run Claude Code plugin manifests, lifecycle hooks, slash-command implementations, or agent definitions. The portable pieces use Zed Agent Skills, project instructions, local tools, and MCP servers.

## Capability matrix

| Claude Code plugin | Zed implementation | Parity | Limitation |
|---|---|---:|---|
| `ponytail` | Global Agent Skill | Direct | None for the instruction workflow |
| `caveman` | Global Agent Skill | Direct | None for the output style |
| `agent-sdk-dev` | Global Agent Skill | Direct | Guidance is portable; still targets Claude Agent SDK code |
| `claude-opus-4-5-migration` | Explicit Agent Skill | Direct | Intentionally manual and task-specific |
| `commit-commands` | Explicit Agent Skill plus Git tools | Partial | Claude slash commands are not installed in Zed |
| `code-review` | Agent Skill plus local diff tools | Direct | No material workflow gap |
| `explanatory-output-style` | Global Agent Skill | Direct | None for the output style |
| `feature-dev` | Agent Skill plus local tools | Direct | No material workflow gap |
| `frontend-design` | Agent Skill plus local tools | Direct | No material workflow gap |
| `hookify` | Agent Skill creating instructions/checks | Partial | Claude lifecycle hooks do not run in Zed |
| `learning-output-style` | Global Agent Skill | Direct | None for the output style |
| `plugin-dev` | Agent Skill for maintaining Claude plugins | Partial | Zed cannot execute Claude plugin manifests |
| `pr-review-toolkit` | Agent Skill plus delegation | Direct | No material workflow gap |
| `ralph-wiggum` | Explicit bounded-loop Agent Skill | Partial | No automatic Stop-hook reinjection |
| `security-guidance` | Explicit Agent Skill and review workflow | Partial | No automatic edit/stop security hooks |
| `claude-mem` | Agent Skill with MCP/file fallback | Partial | Automatic memory injection is not configured |
| `obsidian` | Router plus five native Obsidian skills | Direct | External CLIs still require their own installation |
| `ecc` | Router plus native cross-harness skill pack | Partial | Claude commands, hooks, and agents are not Zed runtimes |

## Activation in Zed

1. Open this repository in Zed so root `AGENTS.md` supplies the thin-orchestrator policy.
2. Run `python setup_model_orchestra.py`, then start a new **Zed Agent** thread and select the `Model Orchestra` profile. The user-scoped profile selects `c-lite-1 / GPT-5.6 Terra` by default and enables all eight orchestration tools. Use `configure_zed_profile.py` alone only to repair the profile without credential prompts.
3. Zed discovers global skills from `~/.agents/skills` and selects them when their descriptions match the task.
4. Add a skill explicitly with `@` in the message editor or through Zed's skill picker when deterministic activation matters, especially for `commit-commands`, `ralph-wiggum`, or migration workflows.
5. Confirm that the `model-orchestra` context server is active in Settings > AI > MCP Servers. Agent Skills and MCP tools are separate capabilities.
6. Reload the Zed window only if the profile or a repaired skill is not visible. A full application restart should not normally be necessary.

Existing Agent Panel threads may retain an older skill catalog or earlier load warning. Starting a new thread is the first refresh step.

## Orchestrator configuration

The project now treats Zed with `c-lite-1 / GPT-5.6 Terra` as the primary host orchestrator, with `c-lite-2` and `c-pro` as manual host fallbacks:

- `configure_zed_profile.py` reproducibly installs the user-scoped `Model Orchestra` Zed Agent profile.
- `AGENTS.md` requires decomposition, delegation, verification, and host-side judgment.
- `server.py` exposes the model-orchestra MCP tools.
- `config.json` keeps the strong security route on Sol and routes mechanical work to cheaper workers.
- `CLAUDE.md` preserves Claude Code as a compatible secondary host.

The host model remains selected by Zed. Neither `AGENTS.md` nor `.mcp.json` can force the model selection.

## Efficiency comparison

### Measured locally

| Measurement | Result | Source |
|---|---:|---|
| Mechanical tasks delegated to `flash` | 6/6 passed | `REPORT.md` |
| Cheap swarm on the same tasks | 6/6 passed | `REPORT.md` |
| Single-worker latency | 66.1 seconds total | `REPORT.md` |
| Swarm latency | 205.7 seconds total | `REPORT.md` |
| Worker usage for six proof tasks | 727 input, 8,153 output tokens | `PROOF.md` |
| Worker API cost at recorded $0.28/M rates | $0.0025 | `PROOF.md` |

These results support using one cheap worker for well-scoped mechanical tasks. On this test set, a swarm added cost and latency without improving the already-perfect result; it should be reserved for hard or uncertain tasks.

### Historical-price comparison

`PROOF.md` prices the same recorded worker tokens against a stated Opus rate of $15/M input and $75/M output, producing $0.6224 versus $0.0025, or about 250x lower worker-generation cost. That is a counterfactual token-price calculation, not a measured Claude Code invoice and not a measured Zed invoice.

### What is not yet measurable

- Zed's configured Terra/Sol gateway prices are not present in the project configuration.
- Claude Code subscription allocation and effective per-task cost are not available from local telemetry.
- Host orchestration tokens, skill-prompt overhead, and MCP round trips are not recorded end to end.
- The current benchmark compares model arms through APIs; it does not execute identical full sessions through both Claude Code and Zed hosts.

Therefore, the evidence supports a worker-cost reduction for delegated output, but not an exact claim that Zed is cheaper than Claude Code overall.

## Fair A/B methodology

For a defensible host comparison, run the same task corpus once through Claude Code and once through Zed with:

- the same repository revision and clean workspace,
- the same model-orchestra worker routes,
- identical correctness tests,
- host input/output tokens or actual billed cost,
- worker tokens and billed cost,
- wall-clock duration,
- tool-call count and retry count,
- human corrections required after each run.

Report total cost as `host cost + worker cost`, and compare only runs that pass the same acceptance tests. Until host billing telemetry is available, keep cost columns marked `unknown` rather than substituting list prices or subscription fees.

## Operational recommendation

Use automatic skills for task-specific guidance and keep the MCP inventory lean. Delegate bounded mechanical work to one cheap worker, use strong models for security and architecture, and use swarms only when independent attempts materially increase the chance of correctness. Invoke hook-derived workflows explicitly because Zed does not reproduce Claude Code lifecycle hooks.
