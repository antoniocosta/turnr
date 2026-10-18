"""turnr: read the turns of a Claude Code session from its JSONL file.

A turn is one prompt the owner typed (or pasted, or that another session typed via herdr), plus
what happened after it. Only prompts count; everything else Claude Code writes as a "user" record
(tool results, task notifications, interrupt markers, command output, slash-command stubs, the
compaction summary) is not a turn.

A compaction shows up as a divider between turns: the turns above it are still on screen and in the
file, but the model now sees them only through the summary.
"""
import json
import os
import re

# Records Claude Code writes with type "user" that are not something the owner typed.
_NOT_PROMPT_PREFIXES = (
    "<task-notification>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "[Request interrupted",
    "<system-reminder>",
    "<agent-message",
)


def projects_dir():
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")), "projects")


def find_session_file(session_id, root=None):
    """The JSONL for a session id, or None. The same id can sit in several project folders (a repo that
    was renamed or moved keeps its old copy), so take the one written most recently: the live one."""
    root = root or projects_dir()
    if not os.path.isdir(root):
        return None
    found = [os.path.join(root, name, session_id + ".jsonl") for name in os.listdir(root)]
    found = [p for p in found if os.path.isfile(p)]
    return max(found, key=os.path.getmtime) if found else None


def _text_of(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return ""
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


# Claude Code stores pasted text wrapped in these tags; its screen shows only what is inside.
_PASTE_TAG = re.compile(r"</?pasted_content(?:\s[^>]*)?>")


def _unwrap(text):
    return _PASTE_TAG.sub("", text).strip()


def _first_line(text, width):
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"\s+", " ", line)
    return line if len(line) <= width else line[: width - 1] + "…"


def read_items(path, offset=0, lineno=0, pending=None, last=None):
    """Items from byte `offset` on: (items, new_offset, new_lineno, pending). An item is
    {"kind": "turn"|"compaction", ...}. `pending` is the prompts typed while the agent was working that it has
    not taken yet (the file's queue-operation records); pass it back in to continue from `offset`.

    Only whole lines are consumed: a half-written last line is left for the next call. Never raises on a
    bad line."""
    items = []
    pending = list(pending or [])
    cur = last        # the turn that assistant records are counted into: "tools" calls and "words" of answer text
    with open(path, "rb") as f:
        f.seek(offset)
        for rawb in f:
            if not rawb.endswith(b"\n"):
                break  # the session is writing this line right now
            start = offset
            offset += len(rawb)
            lineno += 1
            raw = rawb.decode("utf-8", "replace").strip()
            if not raw.startswith("{"):
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue  # a half-written last line while the session is running
            if rec.get("isSidechain"):
                continue  # a subagent's own conversation
            kind, sub = rec.get("type"), rec.get("subtype")
            if kind == "queue-operation":
                op, content = rec.get("operation"), rec.get("content")
                if op == "enqueue" and isinstance(content, str):
                    t = content.strip()
                    if t and not t.startswith(_NOT_PROMPT_PREFIXES) and not t.startswith("/"):
                        pending.append({"text": t, "time": rec.get("timestamp"), "line": lineno})
                elif op == "dequeue" and pending:
                    pending.pop(0)                  # the agent took the first one: its own record follows
                elif op == "remove" and isinstance(content, str):
                    t = content.strip()
                    for k, q in enumerate(pending):
                        if q["text"] == t:
                            del pending[k]
                            break
                elif op == "popAll":
                    pending = []                    # pulled back into the input box
                continue
            if kind == "system" and sub == "compact_boundary":
                meta = rec.get("compactMetadata") or {}
                items.append({"kind": "compaction", "line": lineno, "byte": start, "time": rec.get("timestamp"),
                              "auto": meta.get("trigger") == "auto"})
                continue
            if kind == "assistant" and cur is not None:
                blocks = (rec.get("message") or {}).get("content")
                if isinstance(blocks, list):
                    for b in blocks:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_use":
                            cur["tools"] = cur.get("tools", 0) + 1
                        elif b.get("type") == "text":
                            cur["words"] = cur.get("words", 0) + len((b.get("text") or "").split())
                continue
            if kind == "attachment":
                # A prompt typed while the agent was working is stored as a delivered "queued_command".
                att = rec.get("attachment") or {}
                if att.get("type") != "queued_command":
                    continue
                text = att.get("prompt") if isinstance(att.get("prompt"), str) else ""
                text = text.strip()
                if not text or text.startswith(_NOT_PROMPT_PREFIXES) or text.startswith("/"):
                    continue
                cur = {"kind": "turn", "line": lineno, "byte": start, "time": rec.get("timestamp"), "text": _unwrap(text), "queued": True}
                items.append(cur)
                continue
            if kind != "user" or rec.get("isMeta") or rec.get("isCompactSummary"):
                continue
            text = _text_of(rec.get("message") or {}).strip()
            if not text or text.startswith(_NOT_PROMPT_PREFIXES) or text.startswith("/"):
                continue
            cur = {"kind": "turn", "line": lineno, "byte": start, "time": rec.get("timestamp"), "text": _unwrap(text)}
            items.append(cur)
    return items, offset, lineno, pending


# What a tool call is about, for its one-line summary in the reader: the first of these its input has.
_TOOL_KEYS = ("command", "cmd", "file_path", "path", "notebook_path", "pattern", "url", "query", "description", "prompt")


def tool_line(name, args):
    """A tool call as one line: its name and what it was called on ("Bash  grep -n x lib")."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"command": args}
    if isinstance(args, list):
        args = {"command": args}
    what = ""
    if isinstance(args, dict):
        v = next((args[k] for k in _TOOL_KEYS if args.get(k)), "")
        what = " ".join(map(str, v)) if isinstance(v, list) else str(v)
    return ((name or "tool") + "  " + re.sub(r"\s+", " ", what).strip()).strip()


def read_answer(path, start, end=None):
    """The answer to the turn whose prompt record starts at byte `start`, up to byte `end` (the next turn's, or the
    end of the file): [("text", markdown) | ("tool", one line)], in order. Thinking and tool results are left out."""
    out = []
    with open(path, "rb") as f:
        f.seek(start)
        f.readline()                                    # the prompt's own record
        pos = f.tell()
        for rawb in f:
            pos += len(rawb)
            if end is not None and pos > end:
                break
            try:
                rec = json.loads(rawb)
            except ValueError:
                continue
            if rec.get("type") != "assistant" or rec.get("isSidechain"):
                continue
            for b in (rec.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    out.append(("text", b["text"].strip()))
                elif b.get("type") == "tool_use":
                    out.append(("tool", tool_line(b.get("name"), b.get("input"))))
    return out


def number(items, pending=()):
    """Give each turn its 1-based number and say how many compactions lie above it. Prompts the agent has not
    taken yet go last, as turns marked pending."""
    items = list(items) + [{"kind": "turn", "line": q["line"], "time": q["time"], "text": _unwrap(q["text"]),
                            "queued": True, "pending": True} for q in pending]
    n, comp = 0, 0
    for it in items:
        if it["kind"] == "compaction":
            comp += 1
        else:
            n += 1
            it["n"], it["compactions_above"] = n, comp
    return items


def load(path):
    items, _, _, pending = read_items(path)
    return number(items, pending)


def _state_dir():
    d = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser("~/.cache/turnr")
    os.makedirs(d, exist_ok=True)
    return d


def load_cached(path):
    """Like load(), but remembers what it read: the first call on a session reads the whole file, later
    calls read only the lines added since. A file that shrank or was replaced is read again from the top."""
    cache = os.path.join(_state_dir(), os.path.basename(os.path.dirname(path)) + "__" + os.path.basename(path) + ".json")
    st = os.stat(path)
    state = None
    try:
        with open(cache) as f:
            state = json.load(f)
        if state["size"] > st.st_size or state["ino"] != st.st_ino or state["version"] != 5:
            state = None
    except (OSError, ValueError, KeyError):
        state = None
    if state is None:
        state = {"version": 5, "ino": st.st_ino, "size": 0, "offset": 0, "lineno": 0, "items": [], "pending": []}
    if st.st_size > state["offset"]:
        last = next((x for x in reversed(state["items"]) if x["kind"] == "turn"), None)
        new, state["offset"], state["lineno"], state["pending"] = read_items(
            path, state["offset"], state["lineno"], state["pending"], last)
        state["items"].extend(new)
        state["size"] = st.st_size
        tmp = cache + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, cache)
    return number(state["items"], state["pending"])


def render(items, width=100):
    out = []
    for it in items:
        if it["kind"] == "compaction":
            out.append("── compacted here " + ("(auto) " if it["auto"] else "") + "─" * 20)
        else:
            out.append("%3d  %s" % (it["n"], _first_line(it["text"], width - 5)))
    return "\n".join(out)
