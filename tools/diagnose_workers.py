"""
Ping every worker alias and report health. Pinpoints which aliases 400 on their
provider (dead model id / unsupported param) so config.json can be cleaned up.

Run: python diagnose_workers.py
"""
import concurrent.futures as cf

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from server import WORKERS, resolve, client_for, PROVIDERS, _anthropic_text

REQ_TIMEOUT = 15  # seconds per HTTP call; hung providers fail fast, no stall


def _probe(alias: str) -> tuple[str, str]:
    try:
        provider, model_id = resolve(alias)
    except Exception as e:
        return alias, f"BAD ALIAS: {e}"
    try:
        # Direct call (no failover) with a hard request timeout, so a dead/hung
        # model shows its REAL status instead of masking behind a Groq fallback.
        # Gateway workers use the Anthropic client (no .chat.completions).
        client = client_for(provider)
        msg = [{"role": "user", "content": "Reply with the single word: ok"}]
        if PROVIDERS[provider].get("client") == "anthropic":
            r = client.messages.create(
                model=model_id, max_tokens=16, messages=msg, timeout=REQ_TIMEOUT,
            )
            out = _anthropic_text(r.content).strip()
        else:
            r = client.chat.completions.create(
                model=model_id, messages=msg, temperature=0, timeout=REQ_TIMEOUT,
            )
            out = (r.choices[0].message.content or "").strip()
        first = out.splitlines()[0][:40] if out else "(empty)"
        return alias, f"OK  [{provider}/{model_id}]  -> {first!r}"
    except Exception as e:
        return alias, f"FAIL [{provider}/{model_id}]  -> {type(e).__name__}: {str(e)[:140]}"


PER_ALIAS_TIMEOUT = 25  # seconds; a hung provider must not stall the whole probe


def main() -> None:
    print(f"Probing {len(WORKERS)} worker aliases (<= {PER_ALIAS_TIMEOUT}s each)...\n")
    width = max(len(a) for a in WORKERS)
    ok = fail = 0
    # Submit all up front so probes run concurrently; bound each with a timeout.
    with cf.ThreadPoolExecutor(max_workers=min(len(WORKERS), 8)) as ex:
        futs = {ex.submit(_probe, a): a for a in WORKERS}
        for alias in WORKERS:  # stable config order
            fut = next(f for f, a in futs.items() if a == alias)
            try:
                _, status = fut.result(timeout=PER_ALIAS_TIMEOUT)
            except cf.TimeoutError:
                status = "TIMEOUT (no response / provider hung)"
            except Exception as e:
                status = f"FAIL -> {type(e).__name__}: {str(e)[:120]}"
            ok += status.startswith("OK")
            fail += not status.startswith("OK")
            print(f"  {alias:<{width}}  {status}")
        ex.shutdown(wait=False, cancel_futures=True)
    print(f"\n{ok} OK, {fail} failing. Failing aliases should be fixed or removed from config.json.")


if __name__ == "__main__":
    main()
