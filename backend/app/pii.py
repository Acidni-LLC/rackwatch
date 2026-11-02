"""PII scrubber (DESIGN.md §10). Deterministic and reversible, and it runs before any model call.

Tokens such as <EMAIL_1> are stable per value. The token map lives only in the local `pii_map`
table and is never sent; client email drafts are de-tokenized locally, after a human approves.

Lab IP ranges (RFC 1918 plus 192.0.2.0/24 and 198.51.100.0/24) are infrastructure addresses and pass
through. Everything else is scrubbed, which includes the 203.0.113.0/24 user-laptop addresses
the generator plants (docs/decisions.md D-004).
"""

from __future__ import annotations

import ipaddress
import re
import sqlite3
from collections.abc import Iterable
from typing import Any

LAB_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "192.0.2.0/24",
        "198.51.100.0/24",
    )
)

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CARD = re.compile(r"(?<![\w.-])\d(?:[ -]?\d){12,18}(?![\w-])")
PHONE = re.compile(
    r"(?<![\w.-])(?:\+\d{1,3}[ .-]?\(?\d{2,4}\)?[ .-]?\d{3}[ .-]?\d{3,4}"  # E.164 and +1 forms
    r"|\+\d{10,15}"
    r"|\(?[2-9]\d{2}\)?[ .-]\d{3}[ .-]\d{4}"  # NANP with area code
    r"|[2-9]\d{2}-\d{4})(?![\w-])"  # seven-digit local
)
MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b|\b(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}\b")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.]*\d)")
USER_NAME = re.compile(r"\b([Uu]ser(?:name)?[ :=]+['\"]?)([A-Za-z][\w.-]{1,40})")
GREETING = re.compile(r"\b((?:Hi|Hello|Dear)\s+)([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b")
TOKEN = re.compile(r"<[A-Z]+_\d+>")

# Words that follow "user" in ordinary monitoring text and are not usernames.
USER_STOPWORDS = {
    "a", "account", "accounts", "activity", "agent", "an", "are", "at", "auth", "authentication", "can", "cannot",
    "connection", "connections", "count", "data", "devices", "experience", "failed", "from", "group", "has",
    "have", "id", "in", "is", "login", "logins", "name", "not", "on", "port", "profile", "report", "reported",
    "reports", "session", "sessions", "the", "ticket", "tickets", "to", "traffic", "unable", "vlan", "was", "were",
}  # fmt: skip
GREETING_STOPWORDS = {"All", "Team", "There", "Everyone", "Folks"}


class UnscrubbedPayloadError(RuntimeError):
    """A model call carried text that still matches a PII pattern. meta_client refuses to send it."""


def _is_lab_ip(raw: str) -> bool:
    """True for lab addresses, and for digit runs that are not valid addresses (versions like 17.06.05.01)."""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return True
    return any(ip in net for net in LAB_NETWORKS)


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _is_card(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    return 13 <= len(digits) <= 19 and _luhn(digits)


def names_pattern(names: Iterable[str]) -> re.Pattern | None:
    cleaned = sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    return re.compile(r"(?<![\w.@-])(?:" + "|".join(re.escape(n) for n in cleaned) + r")(?![\w@-])")


def find_unscrubbed(text: str, names: re.Pattern | None = None) -> list[str]:
    """The kinds of hard PII still present in `text`. Empty means clean. Values are never returned."""
    kinds: list[str] = []
    if EMAIL.search(text):
        kinds.append("email")
    if SSN.search(text):
        kinds.append("ssn")
    if any(_is_card(m.group()) for m in CARD.finditer(text)):
        kinds.append("card")
    if PHONE.search(text):
        kinds.append("phone")
    if MAC.search(text):
        kinds.append("mac")
    if any(not _is_lab_ip(m.group()) for m in IPV4.finditer(text)):
        kinds.append("ip")
    if names is not None and names.search(text):
        kinds.append("name")
    return kinds


class PiiScrubber:
    """Reversible tokenizer. Pass a connection to persist the map to `pii_map`; without one it stays in memory."""

    def __init__(self, conn: sqlite3.Connection | None = None, names: Iterable[str] = ()) -> None:
        self.conn = conn
        self.names = names_pattern(names)
        self.by_value: dict[tuple[str, str], str] = {}
        self.by_token: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.items_scrubbed = 0
        if conn is not None:
            for row in conn.execute("SELECT token, kind, value FROM pii_map"):
                self._remember(row["token"], row["kind"], row["value"])

    def _remember(self, token: str, kind: str, value: str) -> None:
        self.by_value[(kind, value)] = token
        self.by_token[token] = value
        number = int(token.rsplit("_", 1)[1].rstrip(">"))
        self.counters[kind] = max(self.counters.get(kind, 0), number)

    def _token(self, kind: str, value: str) -> str:
        self.items_scrubbed += 1
        existing = self.by_value.get((kind, value))
        if existing:
            return existing
        number = self.counters.get(kind, 0) + 1
        token = f"<{kind}_{number}>"
        self._remember(token, kind, value)
        if self.conn is not None:
            self.conn.execute(
                "INSERT OR IGNORE INTO pii_map (token, kind, value) VALUES (?, ?, ?)", (token, kind, value)
            )
        return token

    def scrub(self, text: str | None) -> str:
        if not text:
            return text or ""
        text = EMAIL.sub(lambda m: self._token("EMAIL", m.group()), text)
        text = SSN.sub(lambda m: self._token("SSN", m.group()), text)
        text = CARD.sub(lambda m: self._token("CARD", m.group()) if _is_card(m.group()) else m.group(), text)
        text = PHONE.sub(lambda m: self._token("PHONE", m.group()), text)
        text = MAC.sub(lambda m: self._token("MAC", m.group()), text)
        text = IPV4.sub(lambda m: m.group() if _is_lab_ip(m.group()) else self._token("IP", m.group()), text)
        if self.names is not None:
            text = self.names.sub(lambda m: self._token("PERSON", m.group()), text)
        text = USER_NAME.sub(self._replace_user, text)
        text = GREETING.sub(self._replace_greeting, text)
        return text

    def _replace_user(self, m: re.Match) -> str:
        if m.group(2).lower() in USER_STOPWORDS:
            return m.group()
        return m.group(1) + self._token("PERSON", m.group(2))

    def _replace_greeting(self, m: re.Match) -> str:
        if m.group(2) in GREETING_STOPWORDS:
            return m.group()
        return m.group(1) + self._token("PERSON", m.group(2))

    def scrub_obj(self, obj: Any) -> Any:
        """Scrub every string value in a JSON-like object. Keys are left alone."""
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, list):
            return [self.scrub_obj(v) for v in obj]
        if isinstance(obj, dict):
            return {k: self.scrub_obj(v) for k, v in obj.items()}
        return obj

    def detokenize(self, text: str) -> str:
        """Local only, after human approval. Unknown tokens stay as they are."""
        return TOKEN.sub(lambda m: self.by_token.get(m.group(), m.group()), text or "")


def outbound_text(request: dict) -> list[str]:
    """The text in a request that we authored: instructions, user messages and tool outputs.

    The model's own earlier output (function_call arguments, reasoning, assistant messages) came back from
    the model, so replaying it to the same model discloses nothing new, and images are not text.
    """
    texts: list[str] = []
    if isinstance(request.get("instructions"), str):
        texts.append(request["instructions"])
    items = request.get("input")
    if isinstance(items, str):
        texts.append(items)
        return texts
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call_output":
            texts.append(str(item.get("output", "")))
        elif item.get("role") in ("user", "system", "developer"):
            content = item.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.extend(
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "input_text"
                )
    return texts


def assert_scrubbed(request: dict, names: re.Pattern | None = None) -> None:
    """Defense in depth, called by meta_client on every request. Raises before anything is sent."""
    found: set[str] = set()
    for text in outbound_text(request):
        found.update(find_unscrubbed(text, names))
    if found:
        raise UnscrubbedPayloadError(f"refusing model call: unscrubbed {', '.join(sorted(found))} in payload")
