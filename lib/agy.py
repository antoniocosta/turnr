"""turnr for Antigravity CLI (agy): its whole conversation is in the terminal's own scrollback, so the turns are
read off the screen and the pane is moved with herdr's pane.scroll: one call, however long the answers.

A prompt is a line that starts with "> " (the input box is the same line, empty or holding unsent text, so the
last one is dropped when it sits in the bottom rows with no answer under it). Continuation lines of a wrapped
prompt are indented two spaces. herdr's `recent` read returns one line per scrollback row, so a line's index
gives the scroll offset directly: offset_from_bottom = total - viewport_rows - index puts it on the top row.
"""
import json
import os
import subprocess

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
READ_LINES = 20000


class JumpError(Exception):
    pass


def _run(*args):
    r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=20)
    return r.returncode, r.stdout


def _scroll_info(pane):
    rc, out = _run("pane", "get", pane)
    try:
        return json.loads(out)["result"]["pane"]["scroll"]
    except (ValueError, KeyError, TypeError):
        raise JumpError("cannot read pane %s" % pane)


def _sock():
    return os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")


def _call(method, params):
    import socket
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(10)
    s.connect(_sock())
    s.sendall((json.dumps({"id": "turnr", "method": method, "params": params}) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf or b"{}")


def scan(pane):
    """(items, total_rows, viewport_rows): the numbered turns on the pane's screen and scrollback."""
    rc, out = _run("pane", "read", pane, "--source", "recent", "--lines", str(READ_LINES))
    if rc != 0:
        raise JumpError("cannot read pane %s" % pane)
    lines = out.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    scroll = _scroll_info(pane)
    vp = int(scroll.get("viewport_rows") or 40)
    total = len(lines)
    starts = [i for i, l in enumerate(lines) if l.startswith("> ") and l[2:].strip()]
    items = []
    for k, i in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else total
        text = [lines[i][2:].strip()]
        j = i + 1
        while j < end and lines[j].startswith("  ") and lines[j].strip():
            text.append(lines[j].strip())
            j += 1
        items.append({"kind": "turn", "row": i, "text": " ".join(text), "answered": j < total and any(
            l.strip() for l in lines[j:min(end, j + 3)])})
    # the input box: the last "> " line near the bottom with nothing answering it is not a turn
    if items and not items[-1]["answered"] and items[-1]["row"] >= total - 4:
        items.pop()
    for n, it in enumerate(items, 1):
        it["n"], it["compactions_above"] = n, 0
        it["words"] = 0
        it.pop("answered", None)
    return items, total, vp


def jump(pane, n, log=lambda m: None):
    items, total, vp = scan(pane)
    it = next((x for x in items if x["n"] == n), None)
    if it is None:
        raise JumpError("turn %d is not on agy's screen any more" % n)
    last = it is items[-1]
    offset = 0 if last else max(0, total - vp - it["row"])
    r = _call("pane.scroll", {"pane_id": pane, "offset_from_bottom": offset})
    if "error" in r:
        raise JumpError("scroll failed: %s" % r["error"].get("message", "")[:150])
    return {"turn": n, "sent": 1, "corrections": 0, "landed_on": "turn %d" % n}
