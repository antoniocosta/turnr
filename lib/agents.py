"""The agents turnr knows. Each adapter answers two questions for a pane:

    load(pane, session) -> (items, size)   the numbered turns, and the size in bytes of what they came from (0: n/a)
    jump(pane, n, session) -> dict         scroll the pane to turn n, or raise JumpError
    view(pane, items, prev) -> n or None   the turn the pane's view is at, read off its screen (no keys sent)
    answer(session, items, n) -> list      turn n's answer from the session file: ("text", markdown) and ("tool", line)

`session` is what herdr reports for the pane (an id for Claude Code, Codex; a file path for pi; none for agy).
`keeps_compacted`: whether turns before the last compaction may still be on the agent's screen, so a jump is tried.
To support another agent: write a module with those two functions and register it here.
"""
import os

import agy
import codex
import jump as claude_jump
import nav
import pi
import turns


class Adapter:
    def __init__(self, name, load, jump, keeps_compacted, error, view=lambda pane, items, prev: None,
                 answer=lambda session, items, n: None):
        self.name, self.load, self.jump, self.keeps_compacted, self.error = name, load, jump, keeps_compacted, error
        self.view, self.answer = view, answer


def _claude_load(pane, session):
    path = turns.find_session_file(session) if session else None
    return (turns.load_cached(path), os.path.getsize(path)) if path else ([], 0)


def _claude_jump(pane, n, session):
    return claude_jump.jump_scroll(pane, n)


def _claude_answer(session, items, n):
    path = turns.find_session_file(session) if session else None
    at = next((k for k, it in enumerate(items) if it.get("n") == n and "byte" in it), None)
    if not path or at is None:
        return None
    end = next((it["byte"] for it in items[at + 1:] if "byte" in it), None)
    return turns.read_answer(path, items[at]["byte"], end)


def _path_answer(find, read):
    return lambda session, items, n: read(find(session), n) if find(session) else None


def _agy_load(pane, session):
    return agy.scan(pane)[0], 0


ADAPTERS = {
    "claude": Adapter("claude", _claude_load, _claude_jump, True, claude_jump.JumpError, claude_jump.in_view,
                      _claude_answer),
    "pi": Adapter("pi", pi.load_session, pi.jump, True, pi.JumpError,
                  lambda pane, items, prev: nav.in_view(pane, items, "", prev), _path_answer(pi.session_path, pi.answer)),
    "codex": Adapter("codex", codex.load_session, codex.jump, True, codex.JumpError,
                     lambda pane, items, prev: nav.in_view(pane, items, codex.MARKER, prev),
                     _path_answer(codex.find_session_file, codex.answer)),
    "agy": Adapter("agy", _agy_load, lambda pane, n, session: agy.jump(pane, n), True, agy.JumpError),
}
