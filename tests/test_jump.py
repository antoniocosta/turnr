"""jump.py's reading of a Claude Code screen, on made-up screens (no real session data)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import jump  # noqa: E402
import nav  # noqa: E402


def seg(*texts):
    return [{"kind": "turn", "n": k + 1, "text": t} for k, t in enumerate(texts)]


class Observe(unittest.TestCase):
    S = seg("A", "Failed to load resource", "rename the flag", "A")

    def test_a_prompt_could_be_any_turn_with_its_text(self):
        self.assertEqual(jump.observe("● done\n❯ A\n  answer", self.S), (set(), {0, 3}, 1))

    def test_a_run_of_prompts_is_consecutive_turns(self):
        screen = "  answer\n❯ A\n  answer\n❯ Failed to load resource\n  more"
        self.assertEqual(jump.observe(screen, self.S), (set(), {0}, 2))

    def test_the_first_row_is_the_header(self):
        self.assertEqual(jump.observe("❯ rename the flag\n  answer\n❯ A", self.S), ({2}, {0, 3}, 1))

    def test_a_bare_line_is_not_the_start_of_a_longer_prompt(self):
        s = seg("A", "Also, rename it")
        self.assertEqual(jump.observe("x\n❯ A", s), (set(), {0}, 1))

    def test_a_prompt_of_several_lines_is_shown_joined(self):
        s = seg("[From Stash] D-57 why? This\n  is it", "[From Stash] D-57 why? That")
        self.assertEqual(jump.observe("● x\n❯ [From Stash] D-57 why? This is it", s), (set(), {0}, 1))

    def test_prompts_that_start_alike_are_told_apart_by_the_whole_line(self):
        s = seg("[From Stash (~/dev/Stash) via herdr] D-56 members-only gate. Spec here",
                "[From Stash (~/dev/Stash) via herdr] D-57 station logos. Spec there")
        self.assertEqual(jump.observe("x\n❯ [From Stash (~/dev/Stash) via herdr] D-57 station l…", s), (set(), {1}, 1))

    def test_a_line_half_covered_matches_nothing(self):
        s = seg("[From Stash] D-57 station logos")
        self.assertEqual(jump.observe("x\n❯ [From Stash] D-57 st Jump to bottom (ctrl+End)", s), (set(), set(), 0))

    def test_later_looks_narrow_earlier_ones(self):
        looks = {0: (set(), {2}, 1), 4: (set(), {0, 3}, 1)}       # further up than turn 2, an "A" is the first one
        self.assertEqual(jump._narrow(looks)[4][1], {0})


class Resolve(unittest.TestCase):
    def test_unique_prompts_are_kept(self):
        self.assertEqual(nav.resolve([[0], [2]], 3), [0, 2])

    def test_neighbours_win_over_the_hint(self):
        self.assertEqual(nav.resolve([[1, 4], [2]], 5, near=4), [1, 2])


class InView(unittest.TestCase):
    ITEMS = [{"kind": "turn", "n": k + 1, "text": t} for k, t in enumerate(["first", "second", "third"])]

    def view(self, screen):
        with mock.patch.object(jump, "screen", lambda pane: screen):
            return jump.in_view("p", self.ITEMS)

    def test_a_prompt_in_the_top_half_is_the_turn_in_view(self):
        self.assertEqual(self.view("  answer\n❯ second\n" + "  answer\n" * 10 + "Jump to bottom"), 2)

    def test_a_prompt_low_on_screen_means_the_turn_before_it(self):
        self.assertEqual(self.view("  answer\n" * 10 + "❯ third\n  answer\nJump to bottom"), 2)

    def test_not_scrolled_up_is_the_newest_turn(self):
        self.assertEqual(self.view("  a long answer\n" * 5), 3)

    def test_scrolled_inside_an_answer_with_no_header_says_nothing(self):
        self.assertIsNone(self.view("  answer\n" * 5 + "Jump to bottom"))


class FakeClaude:
    """A Claude Code screen: the conversation as lines, a window of `height` rows scrolled `off` rows up from the
    bottom, PgUp/PgDn by `page` rows. Scrolled up, the first row shows the last prompt above the window that can be a
    header (a prompt typed while Claude worked never is). A click on the first row goes to that prompt."""

    def __init__(self, turns_, answers, no_header=(), height=12, page=5):
        self.lines, self.prompt_at, self.prompt_line = [], {}, []
        for k, (text, length) in enumerate(zip(turns_, answers)):
            self.prompt_at[len(self.lines)] = k not in no_header
            self.prompt_line.append(len(self.lines))
            self.lines += ["❯ " + text[:60] + ("…" if len(text) > 60 else "")] + ["  answer %d.%d" % (k, j) for j in range(length)]
        self.height, self.page, self.off = height, page, 0

    def top(self):
        return max(0, len(self.lines) - self.height - self.off)

    def header(self):
        above = [r for r, can in self.prompt_at.items() if can and r < self.top()]
        return max(above) if above and self.off and self.top() not in self.prompt_at else None

    def text(self):
        rows = self.lines[self.top():self.top() + self.height]
        h = self.header()
        return "\n".join(([self.lines[h]] + rows[1:]) if h is not None else rows)

    def keys(self, pane, text):
        limit = len(self.lines) - self.height
        for _ in range(text.count(jump.PGUP)):
            self.off = min(limit, self.off + self.page)
        for _ in range(text.count(jump.PGDN)):
            self.off = max(0, self.off - self.page)
        if jump.CTRL_END in text:
            self.off = 0
        if "\x1b[<0;10;1M" in text and self.header() is not None:
            self.off = len(self.lines) - self.height - self.header()

    def jump(self, items, n):
        with mock.patch.object(jump, "screen", lambda pane: self.text()), \
                mock.patch.object(jump, "_raw", self.keys), mock.patch.object(jump, "settle", lambda *a, **k: None), \
                mock.patch.object(jump, "_guard", lambda pane: {"session": "s", "status": "idle"}), \
                mock.patch.object(jump, "in_transcript", lambda pane: False), \
                mock.patch.object(jump.turns, "find_session_file", lambda sid: "s.jsonl"), \
                mock.patch.object(jump.turns, "load_cached", lambda path: items), \
                mock.patch.object(jump.time, "sleep", lambda s: None):
            return jump.jump_scroll("p", n)

    def on_screen(self):
        return [ln for ln in self.text().splitlines() if ln.startswith("❯ ")]


class Search(unittest.TestCase):
    # like a real session: a compaction, prompts that start alike, two "A"s, prompts typed while Claude worked
    TEXTS = ["[From Stash] D-55 amendment for Flaky, one pattern for all of it", "keep moving through the queue",
             "A", "[From Stash] Upstream Stash moved", "I can see the app while not signed in",
             "[From Stash] Low priority; take the owner's side", "[Image #8] I see a W as radio logo. Why?",
             "[From Stash] D-56 members-only gate. Spec: here", "Failed to load resource: 401",
             "[From Stash] D-57 station logos. Spec: there", "[From Stash] D-57 addition, measured on the radio",
             "A", "What is the lossless source?"]
    ANSWERS = [40, 30, 6, 2, 9, 3, 25, 2, 2, 8, 20, 18, 4]
    QUEUED = {3, 5, 7, 8, 9}

    def items(self):
        its = [{"kind": "turn", "n": k + 1, "text": t, "queued": k in self.QUEUED} for k, t in enumerate(self.TEXTS)]
        return its[:2] + [{"kind": "compaction"}] + its[2:]

    def test_every_turn_is_reached(self):
        for n in range(1, len(self.TEXTS)):
            fake = FakeClaude(self.TEXTS, self.ANSWERS, self.QUEUED)
            fake.jump(self.items(), n)
            want = fake.lines[fake.prompt_line[n - 1]]
            self.assertIn(want, fake.on_screen(), "turn %d" % n)
            if n - 1 not in self.QUEUED:
                self.assertEqual(fake.lines[fake.top()], want, "turn %d at the top" % n)

    def test_a_second_jump_to_the_same_turn_lands_the_same(self):
        fake = FakeClaude(self.TEXTS, self.ANSWERS, self.QUEUED)
        fake.jump(self.items(), 10)
        first = fake.top()
        fake.jump(self.items(), 10)
        self.assertEqual(fake.top(), first)

    def test_a_turn_no_longer_on_screen_is_reported(self):
        fake = FakeClaude(self.TEXTS[2:], self.ANSWERS[2:], {q - 2 for q in self.QUEUED})   # older turns dropped
        with self.assertRaises(jump.JumpError):
            fake.jump(self.items(), 1)


if __name__ == "__main__":
    unittest.main()
