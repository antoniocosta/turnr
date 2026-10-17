"""turnr: put a Claude Code pane on a chosen turn.

Claude Code runs fullscreen (alternate screen), so herdr has no scrollback to move. Instead the pane gets
Claude's own scroll keys (PgUp/PgDn, Ctrl+End) in batches, and a click on the pinned prompt header, which
jumps to that prompt. Claude only shows the turns after the last compaction, so earlier ones cannot be
jumped to.

Safety: keys go to the pane only while herdr reports an agent there that is idle or working.
Never when it is blocked (a dialog would take the keys), absent (they would land in a shell), or unknown.
"""
import json
import os
import re
import subprocess
import time

import turns

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
KEY_DELAY = float(os.environ.get("TURNR_KEY_DELAY", "0.25"))
SAFE_STATES = ("idle", "working", "done")


class JumpError(Exception):
    pass


def _herdr(*args):
    r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=20)
    return r.returncode, r.stdout


def agent_info(pane):
    rc, out = _herdr("agent", "get", pane)
    try:
        agent = json.loads(out)["result"]["agent"]
    except (ValueError, KeyError, TypeError):
        raise JumpError("no agent in pane %s (keys would land in a shell): not sending anything" % pane)
    if agent.get("agent") != "claude":
        raise JumpError("pane %s runs %r, turnr only drives Claude Code" % (pane, agent.get("agent")))
    return {"status": agent.get("agent_status"),
            "session": (agent.get("agent_session") or {}).get("value")}


def _guard(pane):
    info = agent_info(pane)
    if info["status"] not in SAFE_STATES:
        raise JumpError("agent in %s is %r: not sending keys (a dialog may be open)" % (pane, info["status"]))
    return info


def _keys(pane, *keys):
    _guard(pane)
    rc, out = _herdr("pane", "send-keys", pane, *keys)
    if rc != 0:
        raise JumpError("send-keys failed: %s" % out.strip()[:200])
    time.sleep(KEY_DELAY)


def screen(pane):
    return _herdr("pane", "read", pane, "--source", "visible", "--lines", "200")[1]


TRANSCRIPT_FOOTER = "Showing detailed transcript"


def in_transcript(pane):
    """Claude's transcript view shows this in its footer; ordinary conversation text must not count."""
    return any(ln.strip().startswith(TRANSCRIPT_FOOTER) for ln in screen(pane).splitlines())


def _wait(pane, want, tries=12):
    for _ in range(tries):
        if in_transcript(pane) == want:
            return True
        time.sleep(0.25)
    return False


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def segment(items):
    """The turns Claude can show: those after the last compaction."""
    last = max((i for i, it in enumerate(items) if it["kind"] == "compaction"), default=-1)
    return [it for it in items[last + 1:] if it["kind"] == "turn" and not it.get("pending")]


def _matches(on_screen, turn):
    want = _norm(turn["text"].splitlines()[0] if turn["text"].strip() else "")
    k = min(len(want), len(on_screen), 40)
    return k > 0 and on_screen[:k] == want[:k]


def leave(pane):
    if in_transcript(pane):
        _keys(pane, "q")
        _wait(pane, False)


# ── normal-mode scrolling ────────────────────────────────────────────────────────────────────────
PGUP, PGDN, CTRL_END = "\x1b[5~", "\x1b[6~", "\x1b[1;5F"


def _raw(pane, text):
    _guard(pane)
    rc, out = _herdr("pane", "send-text", pane, text)
    if rc != 0:
        raise JumpError("send-text failed: %s" % out.strip()[:200])


def read_position(pane, seg):
    """(header, visible): `header` is the turn index shown on the first screen row when it is a '❯ prompt' line.
    Scrolled up, Claude pins there the prompt of the turn whose answer the window is inside. `visible` is every
    prompt on screen (turn indexes)."""
    lines = screen(pane).splitlines()
    def idx_of(line):
        text = _norm(line[2:])
        return next((i for i, t in enumerate(seg) if _matches(text, t)), None)
    header = idx_of(lines[0]) if lines and lines[0].startswith("❯ ") else None
    visible = sorted({i for i in (idx_of(l) for l in lines if l.startswith("❯ ")) if i is not None})
    return header, visible


def _click_top_row(pane):
    _raw(pane, "\x1b[<0;10;1M\x1b[<0;10;1m")     # a left click on row 1: Claude jumps to the pinned prompt


def settle(pane, first=0.05, limit=1.5):
    """Wait until Claude's screen stops changing (two identical reads in a row), at most `limit` seconds."""
    time.sleep(first)
    prev, t0 = None, time.time()
    while time.time() - t0 < limit:
        cur = screen(pane)
        if cur == prev:
            return
        prev = cur
        time.sleep(0.04)


HOP_RANGE = 4        # within this many turns above the target, hop with header clicks instead of paging


def align_to_top(pane, seg, t):
    """Turn t's prompt is on screen but not on the top row. Scroll down a page at a time until it slips under the
    top, where Claude pins it as the header, then click the header: the real prompt comes to the top. If the page
    steps overshoot (a very short answer), go back up and leave it on screen. → True when it is at the top."""
    for _ in range(8):
        header, visible = read_position(pane, seg)
        if header == t:
            _click_top_row(pane)
            settle(pane, 0.2)
            h2, v2 = read_position(pane, seg)
            return t in v2
        if t not in visible and header is not None and header > t:
            scroll_keys(pane, PGUP, 1)             # overshot: back, so the prompt is on screen again
            return False
        scroll_keys(pane, PGDN, 1)
    return False


def scroll_keys(pane, keys, count):
    """Send `count` copies of `keys` in one call, then wait for Claude to redraw: first for the screen to differ from
    before (a big batch is chewed through before anything is redrawn), then for it to stop changing. If the view was
    already at its limit nothing changes, so the first wait is bounded."""
    before = screen(pane)
    _raw(pane, keys * count)
    deadline = time.time() + min(3.0, 0.3 + count * 0.004)
    while time.time() < deadline and screen(pane) == before:
        time.sleep(0.03)
    settle(pane)


def jump_scroll(pane, n, log=lambda m: None):
    """Scroll Claude's normal view (PgUp/PgDn, several in one call, never transcript mode) until turn n's prompt
    is the pinned header, then click it: Claude jumps to the real prompt. The last turn goes to the bottom
    instead (the end of its answer). Claude keeps its input box."""
    info = _guard(pane)
    path = turns.find_session_file(info["session"]) if info["session"] else None
    if not path:
        raise JumpError("no session file for pane %s" % pane)
    items = turns.load_cached(path)               # a 600 MB session takes ~4 s to parse cold, 10 ms from the cache
    seg = segment(items)
    target = next((it for it in items if it["kind"] == "turn" and it["n"] == n), None)
    if target is None:
        raise JumpError("no turn %d in this session" % n)
    if target not in seg:
        raise JumpError("turn %d is above the last compaction: Claude no longer shows it, so the pane cannot go there" % n)
    t = seg.index(target)
    if in_transcript(pane):
        leave(pane)
    if t == len(seg) - 1:                         # the newest turn: its answer ends at the bottom
        _raw(pane, CTRL_END)
        settle(pane)
        return {"turn": n, "sent": 1, "corrections": 0, "landed_on": "the end of turn %d" % n}
    # The turn at the top of the window only ever moves one way as we scroll, so this is a search for the scroll
    # position `net` (pages up from the bottom) where that turn is t. lo: the furthest position still below the
    # target (turn at the top is after t); hi: the nearest position past it. Between them: interpolate.
    lo, hi = (0, len(seg)), None
    prev = None                                   # the observation before the last one (net, turn)
    step = 3
    sent, net = 0, 0
    hopped, pages = None, 1
    _raw(pane, CTRL_END)                          # the bottom is a known place
    settle(pane)
    for rnd in range(30):
        header, visible = read_position(pane, seg)
        cur = header if header is not None else (visible[0] if visible else None)
        log("round %d: header=%s visible=%s net=%d lo=%s hi=%s" % (rnd, header, visible, net, lo, hi))
        if t in visible and t != header:
            top = align_to_top(pane, seg, t)
            return {"turn": n, "sent": sent, "corrections": rnd, "landed_on": "turn %d %s" % (n, "at the top" if top else "on screen")}
        if header == t:
            settle(pane, 0.1)
            h1, v1 = read_position(pane, seg)
            if t in v1 and t != h1:
                top = align_to_top(pane, seg, t)
                return {"turn": n, "sent": sent, "corrections": rnd, "landed_on": "turn %d %s" % (n, "at the top" if top else "on screen")}
            if h1 == t:
                _click_top_row(pane)
                settle(pane, 0.2)
                h2, v2 = read_position(pane, seg)
                if t in v2:
                    return {"turn": n, "sent": sent, "corrections": rnd, "landed_on": "turn %d (clicked the header)" % n}
            continue                              # the view moved meanwhile: read again
        if header is not None and header > t and header - t <= HOP_RANGE:
            # Close above the target: hop. Clicking the pinned header jumps to that prompt whatever the length of its
            # answer, and a page up from there is inside the previous turn's answer: two keystrokes per turn, not the
            # length of the answer. If a hop made no progress (the header is the real prompt, or the page stayed in
            # the same answer), page up further instead of clicking again.
            if header == hopped:
                pages += 1
                scroll_keys(pane, PGUP, pages)
            else:
                _click_top_row(pane)
                settle(pane, 0.2)
                pages, hopped = 1, header
                scroll_keys(pane, PGUP, 1)
            sent += pages
            net = 0
            lo, hi, prev, step = (0, len(seg)), None, None, 3
            continue
        if cur is None and net > 0:
            cur = -1                              # scrolled up and no header: above the first prompt
        if cur is not None:
            if cur > t:
                lo = max(lo, (net, cur), key=lambda o: o[0])
            else:
                hi = (net, cur) if hi is None else min(hi, (net, cur), key=lambda o: o[0])
        if cur is None:                           # inside a long answer at the bottom, no header yet
            goal = net + step
        elif hi is None:                          # not past the target yet: gallop up
            if prev is not None and prev[1] != cur and net != prev[0]:
                per_turn = abs(net - prev[0]) / float(abs(prev[1] - cur))   # pages per turn, measured
                goal = net + max(1, int(round(per_turn * (cur - t) * 0.9)))
            else:
                step = max(2, step * 2)           # no progress: the answer is long, double the step
                goal = net + step
        else:                                     # bracketed: interpolate between lo and hi, strictly inside
            (n_lo, c_lo), (n_hi, c_hi) = lo, hi
            frac = (c_lo - t) / float(max(1, c_lo - c_hi))
            goal = n_lo + int(round(frac * (n_hi - n_lo)))
            goal = max(n_lo + 1, min(n_hi - 1, goal)) if n_hi - n_lo > 1 else n_lo
            if net == goal:
                goal = net + (1 if cur > t else -1)
        if cur is not None:
            prev = (net, cur)
        delta = goal - net
        if delta == 0:
            delta = 1 if (cur is None or cur > t) else -1
        scroll_keys(pane, PGUP if delta > 0 else PGDN, abs(delta))
        sent += abs(delta)
        net = goal if goal != net else net + delta
    raise JumpError("could not bring turn %d on screen" % n)
