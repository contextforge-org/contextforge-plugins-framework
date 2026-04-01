# -*- coding: utf-8 -*-
"""Location: ./cpex/framework/session.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0
Authors: Teryl Taylor

Session management for CPEX.

Provides flexible session tracking across tool calls with multiple
resolution strategies. Sessions accumulate state monotonically —
labels only grow, counters only increment, nothing shrinks.

Session Resolution Priority:
  1. Explicit session_id in JWT claims (token-bound, strongest)
  2. Client-provided header (X-CPEX-Session-Id)
  3. Identity-derived (hash of sub + act.sub + aud from token)
  4. No session (graceful degradation — no session analytics)

Usage:
    store = SessionStore()
    resolver = SessionResolver()

    # From identity resolution result
    session_key = resolver.resolve(claims=token_claims, headers=request_headers)
    if session_key:
        session = store.get_or_create(session_key)
        # ... run pipeline ...
        store.merge(session_key, new_labels={"PII"}, tool_name="get_compensation")

See: docs/session-management-design.md
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Header name for client-provided session IDs
SESSION_HEADER = "X-CPEX-Session-Id"


# ---------------------------------------------------------------------------
# Session State
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    """Accumulated session state. All fields grow monotonically.

    Attributes:
        session_id: The session key (derived or explicit).
        created_at: Unix timestamp of session creation.
        labels: Security/data labels — union only, never shrink.
        tool_calls: Total tool invocations in this session.
        tools_seen: Which tools have been called.
        cost: Accumulated cost/budget usage.
        tokens_used: Accumulated token usage.
        trust_domains: Trust boundaries crossed.
        intent_origin: Original user intent (immutable once set).
        intent_current: Current intent (may shift).
        intent_shifts: Number of times intent changed.
        source: How the session was created.
    """

    session_id: str = ""
    created_at: float = 0.0
    labels: set[str] = field(default_factory=set)
    tool_calls: int = 0
    tools_seen: set[str] = field(default_factory=set)
    cost: float = 0.0
    tokens_used: int = 0
    trust_domains: set[str] = field(default_factory=set)
    intent_origin: str | None = None
    intent_current: str | None = None
    intent_shifts: int = 0
    source: str = ""  # "identity", "header", "token_claim", "api"

    def merge(
        self,
        new_labels: set[str] | None = None,
        tool_name: str | None = None,
        cost: float = 0.0,
        tokens: int = 0,
        trust_domain: str | None = None,
        intent: str | None = None,
    ) -> None:
        """Merge new state into the session. All operations are monotonic.

        Args:
            new_labels: Labels to add (union with existing).
            tool_name: Tool that was called (added to tools_seen).
            cost: Cost to add to accumulated total.
            tokens: Tokens to add to accumulated total.
            trust_domain: Trust domain crossed (added to set).
            intent: Current intent (tracks shifts).
        """
        if new_labels:
            self.labels |= new_labels
        if tool_name:
            self.tool_calls += 1
            self.tools_seen.add(tool_name)
        self.cost += cost
        self.tokens_used += tokens
        if trust_domain:
            self.trust_domains.add(trust_domain)
        if intent:
            if self.intent_origin is None:
                self.intent_origin = intent
            if self.intent_current and self.intent_current != intent:
                self.intent_shifts += 1
            self.intent_current = intent

    def to_bag_attrs(self) -> dict[str, Any]:
        """Export session state as flat attributes for the APL AttributeBag.

        Returns:
            Dict of session.* attributes ready for bag.set_*() calls.
        """
        return {
            "session.labels": self.labels.copy(),
            "session.tool_calls": self.tool_calls,
            "session.tools_seen": self.tools_seen.copy(),
            "session.cost": self.cost,
            "session.tokens_used": self.tokens_used,
            "session.trust_domains": self.trust_domains.copy(),
        }


# ---------------------------------------------------------------------------
# Session Store
# ---------------------------------------------------------------------------


class SessionStore:
    """In-memory session store.

    Sessions are keyed by session_id and accumulate state monotonically.
    In production, back with Redis for persistence across restarts.

    Args:
        default_ttl: Session TTL in seconds (0 = no expiry). Default 3600.
        max_sessions: Max sessions before LRU eviction. Default 10000.
    """

    def __init__(self, default_ttl: int = 3600, max_sessions: int = 10000):
        """Initialize the session store.

        Args:
            default_ttl: Session TTL in seconds (0 = no expiry). Default 3600.
            max_sessions: Max sessions before LRU eviction. Default 10000.
        """
        self._sessions: dict[str, SessionState] = {}
        self._access_order: list[str] = []
        self._default_ttl = default_ttl
        self._max_sessions = max_sessions

    def get(self, session_id: str) -> SessionState | None:
        """Get a session by ID. Returns None if not found or expired."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if self._default_ttl > 0:
            age = time.time() - session.created_at
            if age > self._default_ttl:
                del self._sessions[session_id]
                return None
        return session

    def get_or_create(self, session_id: str, source: str = "") -> SessionState:
        """Get existing session or create a new one."""
        session = self.get(session_id)
        if session is not None:
            return session

        # Evict if at capacity
        while len(self._sessions) >= self._max_sessions and self._access_order:
            oldest = self._access_order.pop(0)
            self._sessions.pop(oldest, None)

        session = SessionState(
            session_id=session_id,
            created_at=time.time(),
            source=source,
        )
        self._sessions[session_id] = session
        self._access_order.append(session_id)
        logger.debug("Session created: %s (source=%s)", session_id, source)
        return session

    def merge(
        self,
        session_id: str,
        new_labels: set[str] | None = None,
        tool_name: str | None = None,
        cost: float = 0.0,
        tokens: int = 0,
        trust_domain: str | None = None,
        intent: str | None = None,
    ) -> SessionState | None:
        """Merge state into a session. Returns updated session or None if not found."""
        session = self.get(session_id)
        if session is None:
            return None
        session.merge(
            new_labels=new_labels,
            tool_name=tool_name,
            cost=cost,
            tokens=tokens,
            trust_domain=trust_domain,
            intent=intent,
        )
        return session

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if it existed."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            if session_id in self._access_order:
                self._access_order.remove(session_id)
            return True
        return False

    @property
    def count(self) -> int:
        """Return the number of active sessions."""
        return len(self._sessions)


# ---------------------------------------------------------------------------
# Session Resolver
# ---------------------------------------------------------------------------


class SessionResolver:
    """Determines the session key from request context.

    Resolution priority:
      1. Explicit session_id claim in JWT (token-bound)
      2. Client-provided header (X-CPEX-Session-Id)
      3. Identity-derived (hash of sub + act.sub + aud)
      4. None (no session)

    Args:
        require_session: If True, raise if no session can be resolved.
            If False (default), return None for no-session case.
    """

    def __init__(self, require_session: bool = False):
        """Initialize the session resolver.

        Args:
            require_session: If True, raise ValueError when no session can be resolved.
                If False (default), return None for the no-session case.
        """
        self._require_session = require_session

    def resolve(
        self,
        claims: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[str | None, str]:
        """Resolve a session key from available context.

        Args:
            claims: Decoded JWT claims (from identity resolution).
            headers: HTTP request headers.

        Returns:
            Tuple of (session_key, source) where source is one of:
            "token_claim", "header", "identity", "none".

        Raises:
            ValueError: If require_session is True and no session can be resolved.
        """
        # 1. Explicit session_id in JWT claims (strongest binding)
        if claims and "session_id" in claims:
            return str(claims["session_id"]), "token_claim"

        # 2. Client-provided header
        if headers:
            session_header = headers.get(SESSION_HEADER) or headers.get(SESSION_HEADER.lower())
            if session_header:
                return session_header, "header"

        # 3. Identity-derived
        if claims and "sub" in claims:
            key = self._derive_from_identity(claims)
            return key, "identity"

        # 4. No session
        if self._require_session:
            raise ValueError("No session could be resolved and require_session is True")
        return None, "none"

    def _derive_from_identity(self, claims: dict[str, Any]) -> str:
        """Derive a session key from identity claims.

        Key = hash(subject_id : actor_id : audience)

        This means:
        - Same user + same agent + same gateway = same session
        - Different agent = different session
        - Token refresh doesn't break the session (claims are stable)
        """
        sub = claims.get("sub", "anonymous")
        actor = sub  # default: no delegation
        if "act" in claims and isinstance(claims["act"], dict):
            actor = claims["act"].get("sub", sub)
        aud = claims.get("aud", "default")

        raw = f"{sub}:{actor}:{aud}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
