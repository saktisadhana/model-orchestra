# Model Orchestra Agent Policy

## Goal

Solve the task correctly with the least total context, model calls, and repeated
text. The runtime selects the host model; this repository only supplies tools.

## Routing

1. Answer trivial questions and perform small local edits directly. Do not
   delegate when the delegation prompt/result would cost more than the work.
2. For bounded mechanical work, use one cheap worker (`delegate` or
   `speed-run`) and verify the result locally.
3. Use `batch_delegate` only for two or more substantial, independent tasks.
4. Use draft/refine, specialist pipelines, or a swarm only when complexity,
   uncertainty, or blast radius justifies extra calls. Never swarm routine work.
5. Keep architecture, security judgment, conflict resolution, and final
   acceptance in the host. Never emit unchecked worker output.
6. Load only the smallest relevant skill. Do not inject a full plugin catalog.

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
