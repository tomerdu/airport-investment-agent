"""Startup verification for the agent layer.

Four checks, runnable standalone:

    python -m app.agent.preflight            # offline — makes NO API request
    python -m app.agent.preflight --live     # model lookup + one tiny call

Without `--live` this touches only the filesystem and environment. With it,
two requests are made: a Models API lookup and a generation capped at
`max_tokens=16`, so verifying connectivity costs a fraction of a cent.
"""

from __future__ import annotations

import argparse
import sys

import anthropic

from . import config
from .retry import classify
from .usage import TRACKER


def check_env_excluded_from_git() -> tuple[bool, str]:
    gitignore = config.PROJECT_ROOT / ".gitignore"
    if not gitignore.exists():
        return False, "no .gitignore found"
    rules = [ln.strip() for ln in gitignore.read_text(encoding="utf-8").splitlines()]
    if ".env" not in rules:
        return False, ".env is NOT listed in .gitignore"

    # The template is deliberately un-ignored, so it must never hold a secret.
    example = config.PROJECT_ROOT / ".env.example"
    if example.exists() and "sk-ant-" in example.read_text(encoding="utf-8"):
        return False, ".env.example contains a live key and IS tracked by git"

    return True, ".env ignored; .env.example tracked and free of secrets"


def check_api_key() -> tuple[bool, str]:
    """Confirm the key loads. Never prints or returns the value."""
    info = config.describe_credentials()
    if not info["env_file_present"]:
        return False, f"no .env at {info['env_file']}"
    if not info["api_key_loaded"]:
        return False, "ANTHROPIC_API_KEY missing or empty"
    if not info["api_key_prefix_ok"]:
        return False, "ANTHROPIC_API_KEY does not look like an Anthropic key"
    return True, f"loaded from .env (length {info['api_key_length']}, value not shown)"


def check_model(client: anthropic.Anthropic | None = None) -> tuple[bool, str, str]:
    """Verify the configured model against the live Models API.

    Returns (ok, detail, resolved_model). If the configured id is not
    available, the newest listed Sonnet is selected automatically.
    """
    client = client or anthropic.Anthropic(api_key=config.get_api_key())
    try:
        model = client.models.retrieve(config.MODEL)
        return True, f"'{model.id}' available ({model.display_name})", model.id
    except anthropic.NotFoundError:
        pass
    except anthropic.APIStatusError as exc:
        return False, f"could not verify model: {exc.status_code} {exc.message}", config.MODEL

    available = [m.id for m in client.models.list()]
    sonnets = [m for m in available if "sonnet" in m.lower()]
    if not sonnets:
        return False, f"'{config.MODEL}' not found and no Sonnet available", config.MODEL
    chosen = sonnets[0]  # list() returns newest first
    return (
        True,
        f"'{config.MODEL}' not available; auto-selected '{chosen}'",
        chosen,
    )


def check_connectivity(client: anthropic.Anthropic, model: str) -> tuple[bool, str]:
    """Minimal live call. Capped at a handful of tokens to keep cost ~zero."""
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
    except Exception as exc:  # noqa: BLE001 — classified rather than retried
        decision = classify(exc)
        return False, f"[{decision.category}] {decision.user_message}"

    TRACKER.record(resp, model=model, purpose="preflight")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    u = resp.usage
    return True, (
        f"reply={text!r}  in={u.input_tokens} out={u.output_tokens} tokens "
        f"(model={resp.model}) — est. "
        f"${TRACKER.requests[-1].estimated_cost_usd():.6f}"
    )


def run(live: bool = False) -> int:
    """Offline by default.

    Model verification queries Anthropic's Models API, which is a real network
    request against the configured key — so it belongs behind `--live` along
    with the connectivity probe. Without the flag this touches nothing but the
    local filesystem and environment.
    """
    print(f"=== Agent preflight ({'LIVE' if live else 'offline'}) ===")
    failures = 0

    for name, (ok, detail) in (
        (".env excluded from git", check_env_excluded_from_git()),
        ("API key loaded", check_api_key()),
    ):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<26} {detail}")
        failures += not ok

    if failures:
        print("\nOffline checks failed; skipping the rest.")
        return 1

    if not live:
        print(f"  [SKIP] {'model verified':<26} needs an API call — pass --live")
        print(f"  [SKIP] {'live connectivity':<26} needs an API call — pass --live")
        print("\nOffline checks passed. No Anthropic API request was made.")
        return 0

    client = anthropic.Anthropic(api_key=config.get_api_key(), max_retries=0)

    ok, detail, resolved = check_model(client)
    print(f"  [{'PASS' if ok else 'FAIL'}] {'model verified':<26} {detail}")
    failures += not ok

    if ok:
        ok2, detail2 = check_connectivity(client, resolved)
        print(f"  [{'PASS' if ok2 else 'FAIL'}] {'live connectivity':<26} {detail2}")
        failures += not ok2

    if TRACKER.requests:
        print("\nAPI usage this run:")
        for line in TRACKER.summary_lines():
            print(line)

    print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) failed.'}")
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="make one minimal API call")
    sys.exit(run(live=ap.parse_args().live))
