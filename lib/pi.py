"""turnr for pi (pi.dev coding agent): turns from its session JSONL, the pane moved with PgUp / mouse wheel.

pi's session file (~/.pi/agent/sessions/<cwd>/<ts>_<id>.jsonl, the path herdr reports as the agent's session) has
one {"type":"message","message":{"role":"user",...}} record per prompt, and {"type":"compaction"} records.
pi scrolls its own view (the terminal has no scrollback to use): PgUp is 37 rows, one wheel tick is 1 row.
It pins no prompt header, so the position is read by finding prompts on screen by their text: a prompt is drawn
as a block of rows with a background colour, its first line at the block's second row.
"""
import json
import os

import nav

JumpError = nav.JumpError
KEEPS_COMPACTED = True      # turns before the last compaction can still be jumped to
_memo = {}


def load(path):
    """Numbered items from a pi session file (memoised on size and mtime)."""
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns)
    if _memo.get("key") == key:
        return _memo["items"]
    items, cur = [], None
    with open(path, "rb") as f:
        for raw in f:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if rec.get("type") == "compaction":
                items.append({"kind": "compaction", "auto": True, "time": rec.get("timestamp")})
                continue
            m = rec.get("message") or {}
            role = m.get("role")
            blocks = m.get("content")
            if role == "user":
                text = blocks if isinstance(blocks, str) else "\n".join(
                    b.get("text", "") for b in (blocks or []) if isinstance(b, dict) and b.get("type") == "text")
                text = text.strip()
                if text and not text.startswith("/"):
                    cur = {"kind": "turn", "text": text, "time": rec.get("timestamp")}
                    items.append(cur)
            elif role == "assistant" and cur is not None and isinstance(blocks, list):
                for b in blocks:
                    if b.get("type") == "toolCall":
                        cur["tools"] = cur.get("tools", 0) + 1
                    elif b.get("type") == "text":
                        cur["words"] = cur.get("words", 0) + len((b.get("text") or "").split())
    n, comp = 0, 0
    for it in items:
        if it["kind"] == "compaction":
            comp += 1
        else:
            n += 1
            it["n"], it["compactions_above"] = n, comp
    _memo.update(key=key, items=items)
    return items


# ── the pane ─────────────────────────────────────────────────────────────────────────────────────
def session_path(session):
    """herdr reports pi's session as the file path."""
    return session if session and os.path.isfile(session) else None


def load_session(pane, session):
    path = session_path(session)
    return (load(path), os.path.getsize(path)) if path else ([], 0)


def jump(pane, n, session, log=lambda m: None):
    path = session_path(session)
    if not path:
        raise nav.JumpError("no session file for pane %s" % pane)
    return nav.jump(pane, "pi", n, load(path), log=log)   # pi keeps part of the compacted turns on screen (checked)
