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

# ── OpenCode premium workers and retired compatibility aliases ──
assert resolve("k27") == ("opencode-go", "kimi-k2.7-code")
assert resolve("k3") == ("retired-strong", "kimi-k3-unavailable")
assert resolve("grok") == ("opencode-go", "grok-4.5")
assert resolve("terra") == ("retired-strong", "host-comparison-only")
assert resolve("sol") == ("retired-strong", "security-unavailable")
assert resolve("luna") == ("retired-strong", "host-comparison-only")
assert resolve("orchestrator-fallback") == ("retired-strong", "host-comparison-only")

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


# ── Failover: disabled providers must never be selected. ──
from server import _fallbacks_for, _provider_enabled, WORKERS

for model in ("k26", "k27", "glm", "glm51", "grok", "flash"):
    chain = _fallbacks_for(model)
    assert chain, f"{model} has no failover chain"
    assert all(m in WORKERS for m in chain), f"{model} chain has unknown alias: {chain}"
    assert all(_provider_enabled(resolve(m)[0]) for m in chain), (
        f"{model} chain contains a disabled provider: {chain}"
    )

assert resolve("k27") == resolve("k27-oc")
assert resolve("grok") == resolve("grok-oc")

assert _fallbacks_for("sol") == [], "sol must never downgrade (security floor)"

print(f"ok - all {len(WORKERS)} workers + 2 raw passthroughs + 3 bad inputs "
      "+ enabled-provider failover verified")
