"""codex.py on a made-up Codex rollout file, and the marker handling in nav.find."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import codex  # noqa: E402
import nav  # noqa: E402


def rollout(records):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for r in records:
        f.write(json.dumps(r) + "\n")
    f.close()
    return f.name


def typed(text):
    item = {"type": "UserMessage", "content": [{"type": "text", "text": text}]}
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": item}}


class Load(unittest.TestCase):
    def test_only_what_the_owner_typed_is_a_turn(self):
        context = {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                        "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}]}}
        answer = {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                       "content": [{"type": "output_text", "text": "one two three"}]}}
        call = {"type": "response_item", "payload": {"type": "function_call", "name": "shell"}}
        p = rollout([context, typed("first"), call, answer, typed("second"), {"type": "compacted", "payload": {}}])
        items = codex.load(p)
        self.assertEqual([i["kind"] for i in items], ["turn", "turn", "compaction"])
        self.assertEqual((items[0]["tools"], items[0]["words"]), (1, 3))

    def test_slash_commands_are_not_turns(self):
        self.assertEqual(len(codex.load(rollout([typed("/model"), typed("real")]))), 1)

    def test_session_file_is_found_by_id(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "sessions", "2026", "01", "02"))
        path = os.path.join(d, "sessions", "2026", "01", "02", "rollout-2026-01-02T03-04-05-abc123.jsonl")
        open(path, "w").close()
        os.environ["CODEX_HOME"] = d
        try:
            self.assertEqual(codex.find_session_file("abc123"), path)
            self.assertIsNone(codex.find_session_file("nope"))
        finally:
            del os.environ["CODEX_HOME"]


class Find(unittest.TestCase):
    def test_marker_before_the_prompt_is_ignored(self):
        seg = [{"text": "Fix the login bug"}]
        self.assertEqual(nav.find([("", True), ("› Fix the login bug please", True)], seg, marker="›"), {0: 1})


if __name__ == "__main__":
    unittest.main()
