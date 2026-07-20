# model-orchestra

Opus 4.8 (in Claude Code) = **supervisor**. Tiny models on OpenRouter, NVIDIA,
and OpenCode Go = **workers**. An MCP server gives Opus a `delegate()` tool so it
plans and reviews while cheap models do the grunt work.

Why an MCP tool and not Claude Code subagents: subagents can only run Anthropic
models. Delegating to other providers has to go through a tool. This is it.

## Setup

1. **Keys** — copy `.env.example` to `.env`, fill in your keys:
   ```
   OPENROUTER_API_KEY=...
   NVIDIA_API_KEY=...
   OPENCODE_API_KEY=...
   GROQ_API_KEY=...
   SAMBANOVA_API_KEY=...
   ```
   (You don't need all of them — the server only complains about a provider when
   a worker on it is actually called. Groq and SambaNova are free tiers.)

2. **Deps** — `pip install -r requirements.txt`

3. **Test routing** — `python test_resolve.py` should print `ok`.

4. **Register with Claude Code** (user scope = available in every project):
   ```
   claude mcp add -s user model-orchestra -- python "/absolute/path/to/model-orchestra/server.py"
   ```
   Or just run Claude Code from this folder — `.mcp.json` here is picked up
   automatically. Restart Claude Code, then `/mcp` should list `model-orchestra`.

5. **Make Opus supervise** — `CLAUDE.md` in this folder tells Opus to delegate.
   Working in another project? Copy that block into that project's `CLAUDE.md`,
   or just tell Opus: "delegate grunt work via the model-orchestra tools."

## Editing workers

`config.json` -> `workers` maps short aliases to `provider/model-id`. Change the
model ids to whatever your accounts actually have. Format is always
`provider/<the id that provider expects>`. Get real ids from:
- OpenRouter: https://openrouter.ai/models
- NVIDIA: https://build.nvidia.com/models
- OpenCode Go: https://opencode.ai/zen/go/v1/models

## Two modes of `delegate`

- `agent=False` (default) — one prompt in, one text answer out. Cheap and fast.
- `agent=True` — the worker gets read_file / write_file / run_shell tools and
  loops in a `workspace` dir. Needs a tool-calling model (use `k26`).

## Security note

Agent mode lets a worker model run shell commands in its workspace (that's the
point of agentic coding). Only point it at directories you're OK with it
touching. No sandbox — add one (container / command allowlist) if you delegate
untrusted tasks.
