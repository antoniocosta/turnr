"""Moving an agent's own scrolling view to a prompt, for agents whose terminal UI scrolls itself (pi, Codex): there
is no scrollback for herdr to move, so turnr sends PgUp / PgDn a page at a time and recognises prompts on screen.

A prompt is drawn as a block of rows with a background colour; its first line may carry a marker ("› " in Codex).
The page is smaller than the screen, so no prompt is skipped; which way to go is read from the prompts that are
visible. Then wheel ticks bring the prompt to the top row.
"""
import json
import os
import re
import subprocess
import time

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
SAFE_STATES = ("idle", "working", "done")
PGUP, PGDN, END = "\x1b[5~", "\x1b[6~", "\x1b[1;5F"
WHEEL_DOWN = "\x1b[<65;10;10M"
_SGR = re.compile(r"\x1b\[[0-9;]*m")


class JumpError(Exception):
    pass


def run(*args):
    r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=20)
    return r.returncode, r.stdout


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def guard(pane, agent):
    """The agent in `pane` must be `agent` and idle, done or working: keys never go to a dialog or a shell."""
    rc, out = run("agent", "get", pane)
    try:
        a = json.loads(out)["result"]["agent"]
    except (ValueError, KeyError, TypeError):
        raise JumpError("no agent in pane %s: not sending anything" % pane)
    if a.get("agent") != agent:
        raise JumpError("pane %s runs %r, not %s" % (pane, a.get("agent"), agent))
    if a.get("agent_status") not in SAFE_STATES:
        raise JumpError("agent in %s is %r: not sending keys" % (pane, a.get("agent_status")))


def send(pane, agent, text):
    guard(pane, agent)
    rc, out = run("pane", "send-text", pane, text)
    if rc != 0:
        raise JumpError("send-text failed: %s" % out.strip()[:150])


def screen(pane):
    """Rows of the visible screen: [(text, has_background)]."""
    rc, out = run("pane", "read", pane, "--source", "visible", "--lines", "200", "--format", "ansi")
    rows = out.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    return [(_SGR.sub("", r).rstrip(), "48;2;" in r or "48;5;" in r) for r in rows]


def settle(pane, first=0.08, limit=1.5):
    time.sleep(first)
    prev, t0 = None, time.time()
    while time.time() - t0 < limit:
        cur = screen(pane)
        if cur == prev:
            return cur
        prev = cur
        time.sleep(0.05)
    return prev


def resolve(cands, count, near=None):
    """Which turn each prompt on screen is: `cands` holds, top to bottom, the turn indexes each one's text could be
    (several turns can share a prompt: "A", "yes"). Prompts are drawn in turn order, so an ambiguous one must fall
    between its recognised neighbours; if that still leaves a choice, take the one nearest `near` (where the view was
    last seen), or the newest when there is no hint (a jump starts from the bottom). `count`: turns in the list."""
    got = [c[0] if len(c) == 1 else None for c in cands]
    for k, c in enumerate(cands):
        if got[k] is not None:
            continue
        above = max((g for g in got[:k] if g is not None), default=-1)
        below = min((g for g in got[k + 1:] if g is not None), default=count)
        fit = [i for i in c if above < i < below] or c
        got[k] = min(fit, key=lambda i: (abs(i - near), -i)) if near is not None else max(fit)
    return got


def longest(cands, shown, heads):
    """Of several turns whose prompt starts like the line shown, those whose prompt (`heads`: each one's text, its
    lines joined) agrees with all of it: wrapped, or cut with "…". The same list when none does."""
    if len(cands) < 2:
        return cands
    s = shown.rstrip("…").rstrip()
    full = [i for i in cands if heads[i].startswith(s)]
    return full or cands


def find(rows, seg, marker="", near=None):
    """{index in seg: screen row} of the prompts drawn on screen: a row with a background whose text (after the
    marker) starts the prompt's first line. Same-text prompts are told apart by resolve()."""
    heads = [norm(next((ln for ln in it["text"].splitlines() if ln.strip()), ""))[:40] for it in seg]
    full = [norm(it["text"]) for it in seg]
    hits = []
    for r, (text, bg) in enumerate(rows):
        if not bg:
            continue
        t = norm(text)
        if marker and t.startswith(marker.strip()):
            t = norm(t[len(marker.strip()):])
        if len(t) < 3:
            continue
        c = [i for i, h in enumerate(heads)
             if h and t.startswith(h[:max(3, min(len(h), len(t)))]) and (len(t) >= len(h) or h.startswith(t))]
        c = longest(c, t, full)
        if c:
            hits.append((r, c))
    found = {}
    for (r, _), i in zip(hits, resolve([c for _, c in hits], len(seg), near)):
        found.setdefault(i, r)
    return found


def in_view(pane, items, marker="", prev=None):
    """The number of the turn the agent's view is at, read off its screen (no keys sent): the first prompt in the top
    half, else the turn before the first prompt in view. None when no prompt is in view."""
    seg = [it for it in items if it["kind"] == "turn" and not it.get("pending")]
    near = next((i for i, t in enumerate(seg) if t["n"] == prev), None)
    rows = screen(pane)
    found = find(rows, seg, marker, near)
    if not found:
        return None
    i = min(found)
    if found[i] >= len(rows) // 2 and i > 0:
        i -= 1
    return seg[i]["n"]


def jump(pane, agent, n, items, marker="", log=lambda m: None):
    """Scroll the pane of `agent` to the prompt of turn n (items: the numbered items of its session)."""
    seg = [it for it in items if it["kind"] == "turn"]
    target = next((it for it in seg if it["n"] == n), None)
    if target is None:
        raise JumpError("turn %d is not in this session" % n)
    t = seg.index(target)
    send(pane, agent, END)
    rows = settle(pane)
    if t == len(seg) - 1:
        return {"turn": n, "sent": 1, "landed_on": "the end of turn %d" % n}
    # One page at a time: a page is smaller than the screen, so no prompt is skipped; a longer stride could jump
    # across one and leave us without a direction. Down again only when we have gone past the target.
    sent, direction, prev, near = 1, -1, None, None
    for rnd in range(400):
        found = find(rows, seg, marker, near)
        near = min(found) if found else near
        log("round %d: prompts on screen %s" % (rnd, sorted(found)))
        if t in found:
            for _ in range(6):                      # bring it to the top: wheel ticks move several rows; correct by eye
                r = found.get(t)
                if r is None or r <= 1:
                    break
                send(pane, agent, WHEEL_DOWN * min(12, r - 1))
                sent += 1
                rows = settle(pane)
                found = find(rows, seg, marker, t)
            return {"turn": n, "sent": sent, "landed_on": "turn %d" % n}
        if found and max(found) < t:
            direction = +1                          # past the target (it lies further down)
        elif found and min(found) > t:
            direction = -1
        if rows == prev:
            raise JumpError("turn %d is not on %s's screen (it may have been dropped at a compaction)" % (n, agent))
        prev = rows
        send(pane, agent, PGUP if direction < 0 else PGDN)
        sent += 1
        rows = settle(pane)
    raise JumpError("could not find turn %d on %s's screen" % (n, agent))
