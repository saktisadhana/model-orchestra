# Model Orchestra Host Policy

Claude Code is a supported host for this repository. The runtime selects the
host model; model-orchestra only supplies delegation tools.

## Routing

1. Handle trivial questions and genuinely tiny local edits directly. Tiny means a
   typo, comment/formatting-only change, or one obvious string/value replacement in
   one known file. Do not delegate when that narrow exception costs less to complete
   locally.
2. For substantial non-security repository implementation, you MUST call
   `orchestrate_change(task, workspace=<root>)` before editing. This includes new
   behavior, bug fixes, refactors, tests plus implementation, multi-file changes,
   or work requiring repository inspection. It accepts only the Kimi K3
   `repository-edit` route. If an older MCP server lacks it, call
   `auto_delegate(task, agent=True, workspace=<root>)` once. When uncertain whether
   an edit is tiny, treat it as substantial.
3. Give K3 scoped context and acceptance criteria, then inspect the changed-file
   manifest and diff and run deterministic checks. Claude retains architecture,
   conflict resolution, review, and final acceptance. A summary is not verification.
4. If K3 fails, times out, exhausts its steps, or returns no workspace change, do
   not retry, downgrade, or swarm. Continue locally once and report that fallback.
5. Use one cheap worker for bounded stateless mechanical work. Use `batch_delegate`
   only for two or more substantial independent tasks.
6. Keep security-sensitive implementation and analysis in Claude or the Sol-only
   security route. Never route it to K3 merely because it is coding work.
7. Reserve draft/refine, specialist pipelines, and swarms for complexity,
   uncertainty, or high blast radius. Never swarm routine work.
8. Direct `delegate(model=...)` calls require
   `allow_capability_override=True` to bypass Sol or K3 capability floors; overrides
   are counted in orchestration telemetry.
9. Load only the smallest relevant skill or plugin workflow.

## Context And Failure Policy

- Search first and pass workers only relevant excerpts, constraints, and
  acceptance criteria. Do not forward full conversations or repo overviews.
- Prefer deterministic checks over additional model review.
- Compact at phase boundaries, not repeatedly during an edit.
- Never add raw HTML or full HTTP errors to context.
- Treat context overflow as permanent for that payload. Stop after the first
  failure; do not rotate keys/models or retry compaction blindly.
- After one repeated tool failure, record it once, reduce scope, and continue
  with a local fallback.

## Response Policy

For routine completed work, report only what changed, validation performed, and
a blocker or material residual risk. Keep routine responses under 120 words.
Do not restate the request, host identity, repository architecture, unchanged
capabilities, or command output unless asked.

## Safety

- Never put secrets in prompts, tool arguments, logs, transcripts, or files.
- Keep security analysis on the host or the strong-model `security` pipeline.
- External writes, publication, pushes, paid actions, and credential changes
  require explicit approval.
- `cost_report()` reports worker token usage plus a host-equivalent estimate at
  published list rates. Neither is billed cost; the equivalence assumes the host
  would have produced comparable token counts. Do not quote it as an invoice.
- MCP cannot force Claude or Zed to invoke a tool. These instructions provide the
  host contract; `orchestration_report()` records metadata after invocation and
  never retains prompts, file contents, secrets, or raw errors.
