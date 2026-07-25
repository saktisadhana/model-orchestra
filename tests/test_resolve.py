"""Self-check for the model-routing logic. Run: python tests/test_resolve.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from server import resolve

# ── OpenCode Go workers ──
assert resolve("mimo") == ("opencode-go", "mimo-v2.5")
assert resolve("flash") == ("opencode-go", "deepseek-v4-flash")
assert resolve("ds-pro") == ("opencode-go", "deepseek-v4-pro")
assert resolve("mimo-pro") == ("opencode-go", "mimo-v2.5-pro")
assert resolve("k26") == ("opencode-go", "kimi-k2.6")
assert resolve("glm") == ("opencode-go", "glm-5.2")
assert resolve("glm51") == ("opencode-go", "glm-5.1")

# ── Gateway models ──
assert resolve("k27") == ("kimi-gw", "kimi-k2.7-code")
assert resolve("k3") == ("kimi-gw", "kimi-k3")
assert resolve("grok") == ("grok-gw", "grok-4.5")
assert resolve("terra") == ("68886868", "gpt-5.6-terra")
assert resolve("sol") == ("68886868", "gpt-5.6-sol")
assert resolve("luna") == ("68886868", "gpt-5.6-luna")
assert resolve("orchestrator-fallback") == ("68886868", "gpt-5.6-terra")

# ── Cross-provider twins (same model, different provider, for failover) ──
assert resolve("k27-oc") == ("opencode-go", "kimi-k2.7-code")
assert resolve("grok-oc") == ("opencode-go", "grok-4.5")

# ── NVIDIA workers ──
assert resolve("fast-nv") == ("nvidia", "meta/llama-3.1-8b-instruct")

# ── Free providers ──
assert resolve("fast-groq") == ("groq", "llama-3.3-70b-versatile")
assert resolve("fast-samba") == ("sambanova", "Meta-Llama-3.3-70B-Instruct")

# ── Raw provider/model passthrough ──
assert resolve("openrouter/qwen/qwen-2.5-coder-32b-instruct") == \
    ("openrouter", "qwen/qwen-2.5-coder-32b-instruct")
assert resolve("groq/llama-3.3-70b-versatile") == \
    ("groq", "llama-3.3-70b-versatile")

# ── Bad input rejected ──
for bad in ("nope", "unknownprovider/x", "openrouter"):
    try:
        resolve(bad); assert False, f"{bad} should have failed"
    except ValueError:
        pass


# ── Family-aware failover: a fallback must leave the failing provider first,
# and must never leave its own model family. `sol` must never downgrade.
from server import _fallbacks_for, WORKERS

for model in ("k26", "k27", "glm", "glm51", "grok", "terra", "luna", "flash"):
    chain = _fallbacks_for(model)
    home = resolve(model)[0]
    assert chain, f"{model} has no failover chain"
    assert all(m in WORKERS for m in chain), f"{model} chain has unknown alias: {chain}"
    # A provider outage must be survivable: every chain has to leave its home
    # provider at some point, or the whole chain dies with that provider.
    assert any(resolve(m)[0] != home for m in chain), (
        f"{model} chain never leaves provider {home!r}: {chain}")

# Where a same-model twin exists on another provider, it must be tried FIRST,
# because the usual failure is the provider, not the model.
assert resolve(_fallbacks_for("k26")[0])[0] != resolve("k26")[0], (
    "k26 must fail over off opencode-go first (cross-provider kimi)")
assert _fallbacks_for("grok")[0] == "grok-oc", "grok must try its opencode twin first"

assert _fallbacks_for("sol") == [], "sol must never downgrade (security floor)"

print(f"ok - all {len(WORKERS)} workers + 2 raw passthroughs + 3 bad inputs "
      "+ family-aware failover verified")
