"""turnr for Codex CLI: turns from its rollout file, the pane moved with PgUp (see nav.py).

Codex writes ~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session id>.jsonl, one JSON object per line with
"type" and "payload". What the owner typed is an `event_msg` whose payload is an item_completed of type
"UserMessage" (the `response_item` user messages also hold Codex's own context, such as the AGENTS.md text, so they
are not used). A `compacted` record is a compaction. Prompts are drawn as `› text` on a background.
"""
import glob
import json
import os

import nav
import turns

JumpError = nav.JumpError
KEEPS_COMPACTED = True      # attempt turns above a compaction; the navigator says so if they are not on screen
MARKER = "›"
_TOOL_CALLS = ("function_call", "local_shell_call", "custom_tool_call", "web_search_call", "tool_search_call")
_memo = {}


def sessions_dir():
    return os.path.join(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")), "sessions")


def find_session_file(session_id):
    if not session_id:
        return None
    hits = glob.glob(os.path.join(sessions_dir(), "*", "*", "*", "rollout-*-%s.jsonl" % session_id))
    return max(hits, key=os.path.getmtime) if hits else None


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in (content or []) if isinstance(b, dict) and isinstance(b.get("text"), str))


def load(path):
    """Numbered items from a Codex rollout (memoised on size and mtime)."""
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns)
    if _memo.get("key") == key:
        return _memo["items"]
    items, cur = [], None
    with open(path, "rb") as f:
        for lineno, raw in enumerate(f):
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            kind, p = rec.get("type"), rec.get("payload") or {}
            if not isinstance(p, dict):
                continue
            if kind == "compacted":
                items.append({"kind": "compaction", "auto": True, "time": rec.get("timestamp"), "line": lineno})
            elif kind == "event_msg" and p.get("type") == "item_completed" and (p.get("item") or {}).get("type") == "UserMessage":
                text = _text(p["item"].get("content")).strip()
                if text and not text.startswith("/"):
                    cur = {"kind": "turn", "text": text, "time": rec.get("timestamp"), "line": lineno}
                    items.append(cur)
            elif kind == "response_item" and cur is not None:
                if p.get("type") in _TOOL_CALLS:
                    cur["tools"] = cur.get("tools", 0) + 1
                elif p.get("type") == "message" and p.get("role") == "assistant":
                    cur["words"] = cur.get("words", 0) + len(_text(p.get("content")).split())
    n, comp = 0, 0
    for it in items:
        if it["kind"] == "compaction":
            comp += 1
        else:
            n += 1
            it["n"], it["compactions_above"] = n, comp
    _memo.update(key=key, items=items)
    return items


def answer(path, n):
    """Turn n's answer: [("text", markdown) | ("tool", one line)] (see turns.read_answer)."""
    items = load(path)
    at = next((k for k, it in enumerate(items) if it.get("n") == n), None)
    if at is None:
        return []
    start = items[at]["line"]
    end = next((it["line"] for it in items[at + 1:]), None)
    out = []
    with open(path, "rb") as f:
        for lineno, raw in enumerate(f):
            if lineno <= start:
                continue
            if end is not None and lineno >= end:
                break
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            p = rec.get("payload") or {}
            if rec.get("type") != "response_item" or not isinstance(p, dict):
                continue
            if p.get("type") == "message" and p.get("role") == "assistant" and _text(p.get("content")).strip():
                out.append(("text", _text(p.get("content")).strip()))
            elif p.get("type") == "local_shell_call":
                out.append(("tool", turns.tool_line("shell", (p.get("action") or {}).get("command"))))
            elif p.get("type") in _TOOL_CALLS:
                out.append(("tool", turns.tool_line(p.get("name") or p["type"].replace("_call", ""),
                                                    p.get("arguments") or p.get("input") or p.get("action"))))
    return out


def load_session(pane, session):
    path = find_session_file(session)
    return (load(path), os.path.getsize(path)) if path else ([], 0)


def jump(pane, n, session, log=lambda m: None):
    path = find_session_file(session)
    if not path:
        raise nav.JumpError("no rollout file for pane %s" % pane)
    return nav.jump(pane, "codex", n, load(path), marker=MARKER, log=log)
