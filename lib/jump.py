"""turnr: put a Claude Code pane on a chosen turn.

Claude Code runs fullscreen (alternate screen), so herdr has no scrollback to move. Instead the pane gets
Claude's own scroll keys (PgUp/PgDn, Ctrl+End) in batches, and a click on the pinned prompt header, which
jumps to that prompt. Where the view is comes only from the prompts on screen: they are drawn in turn order
(those before a compaction too), but the pinned header is not always the nearest one, since a prompt typed
while Claude was working never becomes the header, and prompts can share their text ("A", "yes").

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
    """The turns that can be on Claude's screen: every one it has taken (it keeps those above a compaction too)."""
    return [it for it in items if it["kind"] == "turn" and not it.get("pending")]


def _matches(shown, head):
    """Whether a '❯ ' line shows the prompt `head` (its text, lines joined): the line is the whole prompt, or its start
    cut with "…", or its start wrapped at a space. A bare "A" is not "Also, …"; a line half covered by Claude's
    "Jump to bottom" hint matches nothing."""
    if shown.endswith("…"):
        s = shown[:-1].rstrip()
        return bool(s) and head.startswith(s)
    return bool(shown) and (head == shown or head.startswith(shown + " "))


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
    is in view, then bring it to the top (see search()). The last turn goes to the bottom instead (the end of its
    answer). Claude keeps its input box."""
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
        raise JumpError("turn %d is still waiting in the queue" % n)
    t = seg.index(target)
    if in_transcript(pane):
        leave(pane)
    _raw(pane, CTRL_END)                          # the bottom is a known place
    settle(pane)
    if t == len(seg) - 1:                         # the newest turn: its answer ends at the bottom
        return {"turn": n, "sent": 1, "corrections": 0, "landed_on": "the end of turn %d" % n}
    found = search(pane, seg, t, PGUP, log)         # from the bottom up
    if found is None:                             # reached the top short of it: from the top down (pages counted afresh)
        found = search(pane, seg, t, PGDN, log)
    if found is None:                             # no look was sure of anything (long answers, prompts alike): every page
        found = search(pane, seg, t, PGUP, log, crawl=True)
    if found is None:
        raise JumpError("turn %d is not on Claude's screen any more (it may have been dropped at a compaction)" % n)
    sent, rnd, top = found
    return {"turn": n, "sent": sent, "corrections": rnd, "landed_on": "turn %d %s" % (n, "at the top" if top else "on screen")}


SCROLLED_HINT = "Jump to bottom"                  # Claude shows it while its view is scrolled up


def _prompt_rows(text, seg):
    """[(screen row, the turns its '❯ ' prompt could be)], top to bottom, for the prompts that match a turn."""
    heads = [_norm(t["text"]) for t in seg]
    rows = [(r, {i for i, h in enumerate(heads) if _matches(_norm(l[2:]), h)})
            for r, l in enumerate(text.splitlines()) if l.startswith("❯ ")]
    return [(r, c) for r, c in rows if c]


def in_view(pane, items, prev=None):
    """The number of the turn Claude's view is at, read off its screen (no keys sent): the first prompt below the top
    row if it is in the top half, else the turn before it, whose answer fills the top; with no prompt in view, the
    header. None when the screen does not say. `prev`: the turn last seen there, to tell same-text prompts apart."""
    seg = segment(items)
    text = screen(pane)
    rows = _prompt_rows(text, seg)
    header, starts, length = observe(text, seg)
    near = next((i for i, t in enumerate(seg) if t["n"] == prev), None)
    pick = lambda c: min(c, key=lambda i: (abs(i - near), -i)) if near is not None else max(c)
    run = [r for r, _ in rows if r != 0]
    if starts:
        a = pick(starts)
        i = a if run[0] < len(text.splitlines()) // 2 or a == 0 else a - 1
    elif header:
        i = pick(header)
    elif SCROLLED_HINT not in text and seg:
        i = len(seg) - 1                          # not scrolled up: the end of the newest turn
    else:
        return None
    return seg[i]["n"]


def observe(text, seg):
    """One look at the screen, as everything it could mean: (header, starts, length). `header`: the turns the first
    row's prompt could be. The prompts below it are consecutive turns: `starts` is where in the session that run could
    begin, `length` how many there are (0: inside an answer, no prompt in view)."""
    rows = _prompt_rows(text, seg)
    header = rows[0][1] if rows and rows[0][0] == 0 else set()
    run = [c for r, c in rows if r != 0]
    if not run:
        return header, set(), 0
    starts = {a for a in run[0] if all(a + j in run[j] for j in range(len(run)))}
    if not starts:                                # a prompt half covered or drawn differently: trust the first alone
        run, starts = run[:1], set(run[0])
    return header, starts, len(run)


def _narrow(looks, up=True):
    """Each look's meanings, narrowed by the others: the further up a look was taken (larger key), the earlier its run
    starts; a header is a prompt before its own run. `up`: keys count pages up from the bottom (else down from the
    top). A look nothing is left of keeps what it had (a screen drawn oddly)."""
    if not up:
        return {-x: v for x, v in _narrow({-x: v for x, v in looks.items()}).items()}
    cur = {x: (set(h), set(st), ln) for x, (h, st, ln) in looks.items()}
    for _ in range(3):
        for x, (h, st, ln) in cur.items():
            uppers = [max(o[1]) for y, o in cur.items() if y < x and o[1]]
            lowers = [min(o[1]) for y, o in cur.items() if y > x and o[1]]
            hi_, lo_ = min(uppers, default=None), max(lowers, default=None)
            keep = {a for a in st if (hi_ is None or a <= hi_) and (lo_ is None or a >= lo_)
                    and (not h or a > min(h))} or st
            top = max(keep) if keep else hi_          # the header is above its own run
            # and no later than a header further down (not "no earlier than one further up": the first row can be a
            # queued prompt, which is not the header there)
            h_hi = min([max(o[0]) for y, o in cur.items() if y < x and o[0]], default=None)
            cur[x] = ({c for c in h if (top is None or c < top or (not keep and c <= top))
                       and (h_hi is None or c <= h_hi)} or h, keep, ln)
    return cur


def _sides(look, t):
    """Where turn t is from a look, under each of its meanings: "here", "up" or "down" ("?": cannot say)."""
    header, starts, length = look
    if header == {t}:
        return {"here"}
    if starts:
        return {"here" if a <= t < a + length else ("up" if t < a else "down") for a in starts}
    if header and all(h > t for h in header):
        return {"up"}
    return {"?"}


def search(pane, seg, t, away, log, crawl=False):
    """Scroll from the end the view is at (the bottom when `away` is PgUp, the top when PgDn) until turn t's prompt is
    in view. Positions count pages from that end. Gallop away from it until a look shows t is behind, then halve the
    bracket; inside long answers, where a look shows no prompt, crawl through it a page at a time (pages overlap, so a
    page further on that still shows nothing has not passed t). `crawl`: a page at a time all the way, every look
    narrowing the next. → (keys sent, rounds, at the top), or None when the view reached the far end first."""
    further, behind = ("up", "down") if away == PGUP else ("down", "up")
    back = PGDN if away == PGUP else PGUP
    looks, net, step, sent = {}, 0, 1, 0

    def look(rnd):
        txt = screen(pane)
        looks[net] = observe(txt, seg)
        cur = _narrow(looks, away == PGUP)
        side = {}
        for x in sorted(cur):
            s = _sides(cur[x], t)
            if s - {further, "?"} == set() and "?" in s and side.get(x - 1) == further:
                s = {further}                     # nothing in view a page past a "further": still further
            side[x] = s.pop() if len(s) == 1 else "?"
        log("round %d: %s net=%d header=%s starts=%s side=%s" % (rnd, "up" if away == PGUP else "down", net,
            sorted(cur[net][0]), sorted(cur[net][1]), side[net]))
        return txt, cur, side

    for rnd in range(5000 if crawl else 200):
        txt, cur, side = look(rnd)
        if side[net] == "here":
            # Bring it to the top: a page down at a time until it slips under the top, where Claude pins it as the
            # header, then click the header and the real prompt comes to the top. A prompt typed while Claude was
            # working never becomes the header: then a page back (pages overlap, so it is in view again).
            toward = -1 if away == PGUP else 1     # a page down, in this search's count
            for k in range(60):
                header, starts, length = cur[net]
                if k and t in header and not seg[t].get("queued"):
                    # t just slipped under the top: the header is the last prompt above, so not one before t
                    header = {c for c in header if c >= t}
                if header == {t}:
                    _click_top_row(pane)
                    settle(pane, 0.2)
                    return sent + 1, rnd + k, True
                if not (starts and all(a <= t < a + length for a in starts)):
                    scroll_keys(pane, PGUP, 1)
                    return sent + 1, rnd + k, False
                if k == 59:
                    return sent, rnd + k, False   # still in view, not at the top
                scroll_keys(pane, PGDN, 1)
                sent += 1
                net += toward
                txt, cur, side = look(rnd + k)
        lo = max([x for x, v in side.items() if v == further], default=0)
        hi = min([x for x, v in side.items() if v == behind], default=None)
        if crawl:
            if hi is not None:
                raise JumpError("turn %d is not on Claude's screen" % seg[t]["n"])
            goal = net + 1
        elif hi is None:
            goal = max(looks) + step              # not past it yet: gallop
            step = min(step * 2, 32)
        else:
            todo = [x for x in range(lo + 1, hi) if x not in looks]
            if not todo:
                raise JumpError("turn %d is not on Claude's screen (it may have been dropped at a compaction)" % seg[t]["n"])
            mid = (lo + hi) // 2
            goal = mid if mid in todo and side[net] != "?" else todo[0]
        delta = goal - net
        scroll_keys(pane, away if delta > 0 else back, abs(delta))
        sent += abs(delta)
        if delta > 0:
            # one key more: if it moves nothing, the view is at the far end (and the count may be off, if part of
            # the batch was swallowed there): stop, so the caller can count from that end instead
            before = screen(pane)
            scroll_keys(pane, away, 1)
            sent += 1
            if screen(pane) == before:
                return None
            goal += 1
        net = goal
