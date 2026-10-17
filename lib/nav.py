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


def find(rows, seg, marker=""):
    """{index in seg: screen row} of the prompts drawn on screen: a row with a background whose text (after the
    marker) starts the prompt's first line."""
    heads = [norm(next((ln for ln in it["text"].splitlines() if ln.strip()), ""))[:40] for it in seg]
    found = {}
    for r, (text, bg) in enumerate(rows):
        if not bg:
            continue
        t = norm(text)
        if marker and t.startswith(marker.strip()):
            t = norm(t[len(marker.strip()):])
        if len(t) < 3:
            continue
        for i, h in enumerate(heads):
            if h and t.startswith(h[:max(3, min(len(h), len(t)))]) and (len(t) >= len(h) or h.startswith(t)):
                found.setdefault(i, r)
                break
    return found


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
    sent, direction, prev = 1, -1, None
    for rnd in range(400):
        found = find(rows, seg, marker)
        log("round %d: prompts on screen %s" % (rnd, sorted(found)))
        if t in found:
            for _ in range(6):                      # bring it to the top: wheel ticks move several rows; correct by eye
                r = found.get(t)
                if r is None or r <= 1:
                    break
                send(pane, agent, WHEEL_DOWN * min(12, r - 1))
                sent += 1
                rows = settle(pane)
                found = find(rows, seg, marker)
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
