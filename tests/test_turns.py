"""turns.py on made-up Claude Code session files (no real session data)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import turns  # noqa: E402


def write(lines):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for rec in lines:
        f.write(json.dumps(rec) + "\n")
    f.close()
    return f.name


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}, "timestamp": "2026-01-01T00:00:00Z"}


def assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


class ReadItems(unittest.TestCase):
    def test_prompts_become_numbered_turns(self):
        p = write([user("first"), assistant({"type": "text", "text": "one two three"}), user("second")])
        items = turns.load(p)
        self.assertEqual([i["n"] for i in items], [1, 2])
        self.assertEqual(items[0]["words"], 3)

    def test_tool_results_and_stubs_are_not_turns(self):
        result = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}}
        p = write([user("real"), result, user("<local-command-stdout>ok</local-command-stdout>"), user("/clear")])
        self.assertEqual(len(turns.load(p)), 1)

    def test_tool_calls_are_counted_in_their_turn(self):
        p = write([user("go"), assistant({"type": "tool_use", "name": "Bash"}, {"type": "tool_use", "name": "Read"})])
        self.assertEqual(turns.load(p)[0]["tools"], 2)

    def test_compaction_is_a_divider_and_counted_above_later_turns(self):
        boundary = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}}
        p = write([user("a"), boundary, user("b")])
        items = turns.load(p)
        self.assertEqual([i["kind"] for i in items], ["turn", "compaction", "turn"])
        self.assertEqual(items[2]["compactions_above"], 1)
        self.assertTrue(items[1]["auto"])

    def test_queued_prompt_is_pending_until_taken(self):
        p = write([user("a"), {"type": "queue-operation", "operation": "enqueue", "content": "later"}])
        items = turns.load(p)
        self.assertTrue(items[-1]["pending"])
        self.assertEqual(items[-1]["text"], "later")

    def test_half_written_last_line_is_left_for_next_time(self):
        p = write([user("a")])
        with open(p, "a") as f:
            f.write('{"type": "user", "mess')
        items, offset, _, _ = turns.read_items(p)
        self.assertEqual(len(items), 1)
        self.assertLess(offset, os.path.getsize(p))

    def test_sidechain_records_are_ignored(self):
        side = user("subagent prompt")
        side["isSidechain"] = True
        self.assertEqual(len(turns.load(write([user("a"), side]))), 1)

    def test_pasted_text_loses_its_wrapper(self):
        p = write([user('<pasted_content id="ab12">\nFailed to load resource: 401\n</pasted_content id="ab12">'),
                   user('see <pasted_content id="cd34">\nx = 1\n</pasted_content id="cd34"> and fix it')])
        self.assertEqual([i["text"] for i in turns.load(p)], ["Failed to load resource: 401", "see \nx = 1\n and fix it"])


if __name__ == "__main__":
    unittest.main()
