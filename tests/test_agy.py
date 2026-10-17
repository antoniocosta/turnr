"""agy.scan on a made-up screen (herdr is replaced by canned output)."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import agy  # noqa: E402


def fake_run(screen, viewport=10):
    def run(*args):
        if args[:2] == ("pane", "get"):
            return 0, json.dumps({"result": {"pane": {"scroll": {"viewport_rows": viewport}}}})
        return 0, screen
    return run


class Scan(unittest.TestCase):
    def test_prompts_are_the_lines_starting_with_a_chevron(self):
        screen = "\n".join([
            "> first question", "  continued here", "", "an answer", "more answer",
            "> second question", "an answer", "", "", "> ", "",
        ])
        with mock.patch.object(agy, "_run", fake_run(screen)):
            items, total, vp = agy.scan("p")
        self.assertEqual([i["n"] for i in items], [1, 2])
        self.assertEqual(items[0]["text"], "first question continued here")
        self.assertEqual((items[0]["row"], items[1]["row"]), (0, 5))
        self.assertEqual((total, vp), (10, 10))

    def test_unanswered_prompt_near_the_bottom_is_the_input_box(self):
        screen = "\n".join(["> asked", "answer", "", "", "", "", "", "", "> half typed"])
        with mock.patch.object(agy, "_run", fake_run(screen)):
            items, _, _ = agy.scan("p")
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
