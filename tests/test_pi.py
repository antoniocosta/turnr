"""pi.py on a made-up pi session file, and the on-screen prompt matching."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import pi  # noqa: E402


def session(records):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for r in records:
        f.write(json.dumps(r) + "\n")
    f.close()
    return f.name


def msg(role, *blocks):
    return {"type": "message", "message": {"role": role, "content": list(blocks)}}


class Load(unittest.TestCase):
    def test_turns_compaction_and_stats(self):
        p = session([
            {"type": "session", "id": "x"},
            msg("user", {"type": "text", "text": "first"}),
            msg("assistant", {"type": "toolCall"}, {"type": "text", "text": "a b c d"}),
            {"type": "compaction", "summary": "..."},
            msg("user", {"type": "text", "text": "second"}),
        ])
        items = pi.load(p)
        self.assertEqual([i["kind"] for i in items], ["turn", "compaction", "turn"])
        self.assertEqual((items[0]["tools"], items[0]["words"]), (1, 4))
        self.assertEqual(items[2]["n"], 2)
        self.assertEqual(items[2]["compactions_above"], 1)

    def test_slash_commands_are_not_turns(self):
        p = session([msg("user", {"type": "text", "text": "/new"}), msg("user", {"type": "text", "text": "real"})])
        self.assertEqual(len(pi.load(p)), 1)


class Find(unittest.TestCase):
    def test_prompt_is_found_by_its_first_line_on_a_background_row(self):
        seg = [{"text": "Fix the login bug\nmore detail"}, {"text": "Add tests"}]
        rows = [("", False), ("", True), (" Add tests for the parser", True), ("Add tests", False)]
        self.assertEqual(pi._find(rows, seg), {1: 2})

    def test_answer_text_without_a_background_is_not_a_prompt(self):
        seg = [{"text": "Fix the login bug"}]
        self.assertEqual(pi._find([("Fix the login bug", False)], seg), {})


if __name__ == "__main__":
    unittest.main()
