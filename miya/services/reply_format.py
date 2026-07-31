"""Normalize Miya replies for dashboard + WhatsApp (plain, friendly text)."""

from __future__ import annotations

import re

_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_BULLET_RE = re.compile(r"^\s*[-•]\s+", re.MULTILINE)
_NUMBERED_BULLET_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)


def format_miya_reply(text: str | None) -> str:
    """Strip markdown markers and em-dashes; keep warm plain language."""
    if not text:
        return ""
    out = str(text).strip()
    out = _BOLD_RE.sub(r"\1", out)
    out = _ITALIC_RE.sub(r"\1", out)
    out = out.replace("—", ", ")
    out = out.replace("–", ", ")
    out = out.replace("---", "")
    out = _BULLET_RE.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return out.strip()
