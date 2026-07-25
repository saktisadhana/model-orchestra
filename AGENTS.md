# Model Orchestra Agent Policy

## Goal

Solve the task correctly with the least total context, model calls, and repeated
text. The runtime selects the host model; this repository only supplies tools.

## Routing

1. Answer trivial questions and perform genuinely tiny local edits directly. Tiny
   means a typo, comment/formatting-only change, or one obvious string/value
   replacement in one known file. Do not delegate when that narrow exception costs
   less to complete locally.
2. For substantial non-security repository implementation, you MUST call
   `orchestrate_change(task, workspace=<root>)` before editing. This includes new
   behavior, bug fixes, refactors, tests plus implementation, multi-file changes,
   or any task requiring repository inspection. The tool accepts only the Kimi K3
   `repository-edit` route. If an older server lacks it, use
   `auto_delegate(task, agent=True, workspace=<root>)` once. When uncertain whether
   an edit is tiny, treat it as substantial.
3. Give K3 scoped context and acceptance criteria. After it finishes, inspect the
   changed-file manifest and diff, then run deterministic checks locally; the host
   owns architecture, conflict resolution, review, and final acceptance. A worker
   summary is a handoff, not proof.
4. If K3 fails, times out, exhausts its steps, or returns no workspace change, do
   not retry, downgrade, or swarm. Continue locally once and state the fallback in
   the final response.
5. For bounded stateless mechanical work, use one cheap worker (`delegate` or
   `speed-run`) and verify the result locally. Use `batch_delegate` only for two or
   more substantial, independent tasks.
6. Keep security-sensitive implementation and analysis on the host or the Sol-only
   security route. Never route it to K3 merely because it is coding work.
7. Use draft/refine, specialist pipelines, or a swarm only when complexity,
   uncertainty, or blast radius justifies extra calls. Never swarm routine work.
8. Direct `delegate(model=...)` calls do not bypass capability floors unless
   `allow_capability_override=True` is explicitly set; that override is audited.
9. Load only the smallest relevant skill. Do not inject a full plugin catalog.

## Context Budget

1. Search first; read targeted file ranges. Do not reread attached/current files.
2. Pass workers only the task, relevant excerpts, constraints, and acceptance
   criteria. Do not forward the full conversation or repository overview.
3. Prefer deterministic tests over model-based review. Validate the changed
   surface first; broaden only when risk requires it.
4. Compact at phase boundaries (research to implementation, or completed
   milestone), never repeatedly in the middle of an edit.
5. Never add raw HTML or full HTTP errors to context. Keep status, URL, and one
   short diagnostic.
6. Context overflow is permanent for that payload. Stop after the first error;
   do not rotate keys/models or retry compaction blindly.
7. After one repeated tool failure, record it once, reduce scope, and use a local
   fallback.

## Response Budget

For routine completed work, report only:

- what changed;
- validation performed;
- a blocker or material residual risk, if one exists.

Keep routine final responses under 120 words. Do not repeat the request, explain
who you are, restate the repository architecture, list unchanged capabilities,
or reproduce command output unless asked. Omit headings when one short paragraph
is enough. Progress updates are for substantive long-running work only.

## Safety

- Never include secrets in prompts, tool arguments, logs, transcripts, or files.
- If a secret appears, redact it without repeating it and require rotation.
- Keep security analysis on the host or strong-model security pipeline.
- External writes, publication, pushes, paid actions, and credential changes
  require explicit approval.
- MCP cannot force Zed or another host to invoke a tool. These instructions are
  the host-side enforcement layer; `orchestration_report()` supplies metadata-only
  evidence after invocation, never prompts, file contents, secrets, or raw errors.
