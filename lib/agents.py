"""The agents turnr knows. Each adapter answers two questions for a pane:

    load(pane, session) -> (items, size)   the numbered turns, and the size in bytes of what they came from (0: n/a)
    jump(pane, n, session) -> dict         scroll the pane to turn n, or raise JumpError

`session` is what herdr reports for the pane (an id for Claude Code, Codex; a file path for pi; none for agy).
`keeps_compacted`: whether turns before the last compaction may still be on the agent's screen, so a jump is tried.
To support another agent: write a module with those two functions and register it here.
"""
import os

import agy
import codex
import jump as claude_jump
import pi
import turns


class Adapter:
    def __init__(self, name, load, jump, keeps_compacted, error):
        self.name, self.load, self.jump, self.keeps_compacted, self.error = name, load, jump, keeps_compacted, error


def _claude_load(pane, session):
    path = turns.find_session_file(session) if session else None
    return (turns.load_cached(path), os.path.getsize(path)) if path else ([], 0)


def _claude_jump(pane, n, session):
    return claude_jump.jump_scroll(pane, n)


def _agy_load(pane, session):
    return agy.scan(pane)[0], 0


ADAPTERS = {
    "claude": Adapter("claude", _claude_load, _claude_jump, False, claude_jump.JumpError),
    "pi": Adapter("pi", pi.load_session, pi.jump, True, pi.JumpError),
    "codex": Adapter("codex", codex.load_session, codex.jump, True, codex.JumpError),
    "agy": Adapter("agy", _agy_load, lambda pane, n, session: agy.jump(pane, n), True, agy.JumpError),
}
