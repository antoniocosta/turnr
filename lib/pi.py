"""turnr for pi (pi.dev coding agent): turns from its session JSONL, the pane moved with PgUp / mouse wheel.

pi's session file (~/.pi/agent/sessions/<cwd>/<ts>_<id>.jsonl, the path herdr reports as the agent's session) has
one {"type":"message","message":{"role":"user",...}} record per prompt, and {"type":"compaction"} records.
pi scrolls its own view (the terminal has no scrollback to use): PgUp is 37 rows, one wheel tick is 1 row.
It pins no prompt header, so the position is read by finding prompts on screen by their text: a prompt is drawn
as a block of rows with a background colour, its first line at the block's second row.
"""
import json
import os
import re
import subprocess
import time

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
SAFE_STATES = ("idle", "working")
PGUP, PGDN, END = "\x1b[5~", "\x1b[6~", "\x1b[1;5F"
WHEEL_UP, WHEEL_DOWN = "\x1b[<64;10;10M", "\x1b[<65;10;10M"
_SGR = re.compile(r"\x1b\[[0-9;]*m")
_memo = {}


class JumpError(Exception):
    pass


def _run(*args):
    r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=20)
    return r.returncode, r.stdout


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


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
def _guard(pane):
    rc, out = _run("agent", "get", pane)
    try:
        a = json.loads(out)["result"]["agent"]
    except (ValueError, KeyError, TypeError):
        raise JumpError("no agent in pane %s: not sending anything" % pane)
    if a.get("agent") != "pi":
        raise JumpError("pane %s runs %r, not pi" % (pane, a.get("agent")))
    if a.get("agent_status") not in SAFE_STATES:
        raise JumpError("agent in %s is %r: not sending keys" % (pane, a.get("agent_status")))
    return (a.get("agent_session") or {}).get("value")


def _send(pane, text):
    _guard(pane)
    rc, out = _run("pane", "send-text", pane, text)
    if rc != 0:
        raise JumpError("send-text failed: %s" % out.strip()[:150])


def _screen(pane):
    """Rows of the visible screen: [(text, has_background)]."""
    rc, out = _run("pane", "read", pane, "--source", "visible", "--lines", "200", "--format", "ansi")
    rows = out.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    return [(_SGR.sub("", r).rstrip(), "48;2;" in r or "48;5;" in r) for r in rows]


def _settle(pane, first=0.08, limit=1.5):
    time.sleep(first)
    prev, t0 = None, time.time()
    while time.time() - t0 < limit:
        cur = _screen(pane)
        if cur == prev:
            return cur
        prev = cur
        time.sleep(0.05)
    return prev


def _find(rows, seg):
    """{index in seg: screen row} of the prompts drawn on screen: a row with a background whose text starts the
    prompt's first line."""
    heads = [_norm(next((ln for ln in it["text"].splitlines() if ln.strip()), ""))[:40] for it in seg]
    found = {}
    for r, (text, bg) in enumerate(rows):
        if not bg:
            continue
        t = _norm(text)
        if len(t) < 3:
            continue
        for i, h in enumerate(heads):
            if h and t.startswith(h[:max(3, min(len(h), len(t)))]) and (len(t) >= len(h) or h.startswith(t)):
                found.setdefault(i, r)
                break
    return found


def jump(pane, n, path, log=lambda m: None):
    items = load(path)
    seg = [it for it in items if it["kind"] == "turn"]     # pi keeps compacted turns in its view (checked)
    target = next((it for it in seg if it["n"] == n), None)
    if target is None:
        raise JumpError("turn %d is not in this session" % n)
    t = seg.index(target)
    _send(pane, END)
    rows = _settle(pane)
    if t == len(seg) - 1:
        return {"turn": n, "sent": 1, "landed_on": "the end of turn %d" % n}
    # One page at a time: a page is 37 rows of a 47-row screen, so no prompt is skipped over; a longer stride could
    # jump across one and leave us without a direction. Down again only when we have gone past the target.
    sent, direction, prev = 1, -1, None
    for rnd in range(400):
        found = _find(rows, seg)
        log("round %d: prompts on screen %s" % (rnd, sorted(found)))
        if t in found:
            # bring the prompt to the top (its block's blank first row at the very top): wheel ticks move several rows
            # each, so go by what the screen shows and correct
            for _ in range(6):
                r = found.get(t)
                if r is None or r <= 1:
                    break
                _send(pane, WHEEL_DOWN * min(12, r - 1))
                sent += 1
                rows = _settle(pane)
                found = _find(rows, seg)
            return {"turn": n, "sent": sent, "landed_on": "turn %d" % n}
        if found and max(found) < t:
            direction = +1                          # past the target (it lies further down)
        elif found and min(found) > t:
            direction = -1
        if rows == prev:
            raise JumpError("turn %d is not on pi's screen (pi may have dropped it at a compaction)" % n)
        prev = rows
        _send(pane, PGUP if direction < 0 else PGDN)
        sent += 1
        rows = _settle(pane)
    raise JumpError("could not find turn %d on pi's screen" % n)
