"""Agent configuration and credential loading.

The API key is read from the environment only. It is never logged, never
echoed, never written into a response, and never included in an error message.
`describe_credentials()` exists so setup can be verified without exposing the
value.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# .env lives at the project root (one level above backend/). It is gitignored.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

# Model is configurable per the approved decision. Default is the current
# Sonnet; `verify_model()` checks it against the live Models API at startup.
DEFAULT_MODEL = "claude-sonnet-5"
MODEL = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

# Sonnet 5 rejects `temperature`/`top_p` (400) and runs adaptive thinking when
# `thinking` is omitted. Depth is controlled with output_config.effort instead.
EFFORT = os.environ.get("AGENT_EFFORT", "medium").strip() or "medium"

MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "8000"))

# Hard ceiling on tool-calling round trips per user turn. Protects against a
# runaway loop burning tokens; the agent answers from what it has and says
# what it could not finish.
MAX_TOOL_HOPS = int(os.environ.get("AGENT_MAX_TOOL_HOPS", "5"))

# Turns of conversation history retained per session.
MAX_HISTORY_TURNS = int(os.environ.get("AGENT_MAX_HISTORY_TURNS", "12"))


class MissingCredentialsError(RuntimeError):
    pass


def get_api_key() -> str:
    """Return the API key, or raise without revealing anything about it."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise MissingCredentialsError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add "
            "your key (get one at https://console.anthropic.com/settings/keys)."
        )
    return key


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def describe_credentials() -> dict[str, object]:
    """Safe-to-log credential status. Never contains the key itself."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return {
        "env_file": str(ENV_PATH),
        "env_file_present": ENV_PATH.exists(),
        "api_key_loaded": bool(key),
        "api_key_length": len(key),
        "api_key_prefix_ok": key.startswith("sk-ant-") if key else False,
        "model": MODEL,
        "effort": EFFORT,
        "max_tokens": MAX_TOKENS,
        "max_tool_hops": MAX_TOOL_HOPS,
    }
