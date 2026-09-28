"""Per-session conversation memory.

In-process and deliberately simple — Redis would add a dependency to the demo
without changing behaviour at this scale.

Three pieces of state make follow-ups work without re-asking:

* `messages`    — the API conversation, trimmed to a turn budget
* `focus`       — airports the last answer was about ("why?", "and Boston?")
* `last_ranking`— ordered codes, so "the second one" resolves to an airport
* `assumptions` — interpretations already stated, re-surfaced on request
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import config


@dataclass
class Session:
    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    focus_airports: list[str] = field(default_factory=list)
    last_ranking: list[str] = field(default_factory=list)
    last_comparison: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    turns: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    # Numbers from every tool result in this session, plus the user's own
    # figures. The audit checks against this rather than the current turn
    # alone: a follow-up that re-quotes a figure fetched three turns ago is
    # legitimate reuse, not fabrication.
    known_numbers: set[float] = field(default_factory=set)
    audit_payloads: list[Any] = field(default_factory=list)

    def remember_numbers(self, payload: Any) -> None:
        from .audit import collect_numbers

        collect_numbers(payload, self.known_numbers)
        self.audit_payloads.append(payload)
        del self.audit_payloads[:-40]

    def note_assumption(self, text: str) -> None:
        if text and text not in self.assumptions:
            self.assumptions.append(text)
            del self.assumptions[:-8]

    def set_focus(self, codes: list[str]) -> None:
        """Replace focus with the airports this turn was about."""
        seen: list[str] = []
        for c in codes:
            c = (c or "").upper()
            if c and c not in seen:
                seen.append(c)
        if seen:
            self.focus_airports = seen[:12]

    def set_ranking(self, codes: list[str]) -> None:
        if codes:
            self.last_ranking = [c.upper() for c in codes][:25]

    def set_comparison(self, codes: list[str]) -> None:
        """Remember the airports last compared, so 'add X to that comparison'
        resolves even after several intervening turns changed the focus."""
        if codes:
            self.last_comparison = [c.upper() for c in codes][:8]

    @staticmethod
    def _is_turn_start(msg: dict[str, Any]) -> bool:
        """True for a plain user message — the start of a conversational turn.

        A `tool_result` message also has role 'user', so role alone does not
        identify a turn boundary.
        """
        if msg.get("role") != "user":
            return False
        content = msg.get("content")
        if isinstance(content, str):
            return True
        return isinstance(content, list) and all(
            b.get("type") != "tool_result" for b in content
        )

    def trim(self) -> None:
        """Keep the last `MAX_HISTORY_TURNS` conversational turns.

        Counts actual turn boundaries, not raw messages. An earlier version
        used `MAX_HISTORY_TURNS * 2` as a message budget, which assumed two
        messages per turn — but a tool-using turn is four or more (user,
        assistant tool_use, user tool_result, assistant text). A "12-turn"
        budget therefore retained only about six real turns, and a follow-up
        like "what if we used 1,500 miles instead?" found the long-haul tool
        result had already been trimmed away.

        Cuts only at turn boundaries: a `tool_use` block must always be
        followed by its `tool_result`, so splitting an exchange would make the
        next request invalid.
        """
        starts = [i for i, m in enumerate(self.messages) if self._is_turn_start(m)]
        if len(starts) <= config.MAX_HISTORY_TURNS:
            return
        cut = starts[-config.MAX_HISTORY_TURNS]
        del self.messages[:cut]

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turns": self.turns,
            "focus_airports": list(self.focus_airports),
            "last_ranking": list(self.last_ranking),
            "last_comparison": list(self.last_comparison),
            "assumptions": list(self.assumptions),
            "message_count": len(self.messages),
            "created_at": self.created_at,
        }


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str | None) -> Session:
        with self._lock:
            if session_id and session_id in self._sessions:
                return self._sessions[session_id]
            sid = session_id or uuid.uuid4().hex[:16]
            session = Session(session_id=sid)
            self._sessions[sid] = session
            return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def reset(self, session_id: str) -> Session:
        with self._lock:
            self._sessions.pop(session_id, None)
            session = Session(session_id=session_id)
            self._sessions[session_id] = session
            return session

    def __len__(self) -> int:
        return len(self._sessions)
