# Model Orchestra Host Policy

Claude Code is a supported host for this repository. The runtime selects the
host model; model-orchestra only supplies delegation tools.

## Routing

1. Handle trivial questions and small local edits directly when delegation would
   add more prompt and result tokens than it saves.
2. Use one cheap worker for bounded mechanical work, then verify the result.
3. Use `batch_delegate` only for two or more substantial independent tasks.
4. Reserve draft/refine, specialist pipelines, and swarms for complexity,
   uncertainty, or high blast radius. Never swarm routine work.
5. Keep architecture, security judgment, conflict resolution, and final
   acceptance in the host. Never return unchecked worker output.
6. Load only the smallest relevant skill or plugin workflow.

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
