"""Self-check for the model-routing logic. Run: python test_resolve.py"""
from server import resolve

# ── OpenCode Go workers ──
assert resolve("mimo") == ("opencode-go", "mimo-v2.5")
assert resolve("flash") == ("opencode-go", "deepseek-v4-flash")
assert resolve("ds-pro") == ("opencode-go", "deepseek-v4-pro")
assert resolve("mimo-pro") == ("opencode-go", "mimo-v2.5-pro")
assert resolve("k27") == ("opencode-go", "kimi-k2.7-code")
assert resolve("k26") == ("opencode-go", "kimi-k2.6")
assert resolve("glm") == ("opencode-go", "glm-5.2")
assert resolve("glm51") == ("opencode-go", "glm-5.1")
assert resolve("grok") == ("opencode-go", "grok-4.5")
assert resolve("k3") == ("opencode-go", "kimi-k3")

# ── NVIDIA workers ──
assert resolve("fast-nv") == ("nvidia", "meta/llama-3.1-8b-instruct")
assert resolve("glm-nv") == ("nvidia", "z-ai/glm-5.2")

# ── OpenRouter workers ──
assert resolve("fast-free") == ("openrouter", "meta-llama/llama-3.1-8b-instruct:free")
assert resolve("qwen-free") == ("openrouter", "qwen/qwen3-coder:free")

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

print("ok — all 16 workers + 2 raw passthroughs + 3 bad inputs verified")
