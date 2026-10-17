# turnr

A [herdr](https://herdr.dev) plugin: a side panel that lists the turns of the coding agent next to it, so you can
jump back to any earlier prompt with a click instead of scrolling for it.

Works with **Claude Code**, **pi** and **Antigravity CLI (agy)**.

## Why

Chat-style agent UIs are a single long scroll. Two things make that painful:

- **Queuing.** While an agent works you keep typing: another prompt, a correction, a follow-up. Soon dozens of turns
  have piled up, each followed by pages of tool output, and finding "what did I actually ask for the third one?"
  means scrolling back through all of it, again and again.
- **Looking something up.** You want to reread an earlier prompt, or the answer under it, or copy a line you wrote an
  hour ago. The only way is to scroll up and hunt.

turnr keeps your own prompts in a narrow panel next to the agent, numbered, each with the length of its answer and its
tool calls. Click one and the agent's pane scrolls to it.

```
─ 97 ──────────── 412 words · 9 tools ─
 Add a retry to the upload step and
 make the timeout configurable…
─ 98 ────────────── 2.1k words · 68 tools ─
 Now the same for the download path,
 and write the test…
─ 99 ─────────────────── ~ waiting ─
 also rename the flag to --retries
─ turnr · @a1b2c3d ──────────────────────
pane w1:p1 · 99 turns · 2 compacted · 1.7 MB
```

## Install

```
herdr plugin install antoniocosta/turnr
```

Then bind a key in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+ctrl+t"
type = "plugin_action"
command = "turnr.toggle"
description = "turnr"
```

The key opens the panel beside the focused agent pane, or closes it if it is open. Needs herdr 0.9.4 or newer, Linux or
macOS, and Python 3 (standard library only).

## Using it

| Key | |
| --- | --- |
| click, or `Enter` | scroll the agent's pane to that turn and focus it |
| `j` / `k`, arrows, `PgUp` / `PgDn`, `g` / `G` | move through the list |
| `y` | copy the selected turn's text (`pbcopy`, `$TURNR_COPY`, or the terminal clipboard via OSC 52) |
| `m` | let the terminal select text instead of the mouse driving the panel (and back) |
| `q` | close |

The turn the agent's view is at is highlighted; hovering or moving the cursor highlights another. The newest turn goes
to the end of its answer. A prompt typed while the agent was busy shows at once as `~ waiting`. Dividers mark
compactions.

Colours are herdr's own theme, role for role as in its sidebar (read from your herdr config), so the panel matches
whatever theme you use.

Settings, as environment variables: `TURNR_WIDTH` (panel width in text columns, default 48), `TURNR_COPY` (a command
that reads the text on stdin), `TURNR_RULE` (a theme role or `#rrggbb` for the faint rule between turns),
`TURNR_HOVER_SECONDS`.

## How it works, per agent

| Agent | The list comes from | The pane is moved with |
| --- | --- | --- |
| Claude Code | its session file (`~/.claude/projects/…`), read incrementally and cached | PgUp / Ctrl+End in batches, then a click on the pinned prompt header, which jumps to that prompt |
| pi | its session file (herdr reports the path) | PgUp one page at a time, mouse-wheel ticks to put the prompt on the top row; prompts are recognised on screen by their text |
| agy | the screen: the `> ` prompt lines in herdr's scrollback | herdr's `pane.scroll`, a single exact call |

Limits:

- Claude Code shows only the turns after its last compaction, so earlier turns are listed (click to read them in the
  panel) but the pane cannot go there. pi keeps part of the compacted turns on screen; turnr tells you when one is
  gone instead of guessing.
- agy: only what is in the terminal's scrollback can be listed. No word and tool counts.
- Claude Code must be in its fullscreen mode (`"tui": "fullscreen"`) for the pinned header that jumps rely on.
- pi jumps page one page at a time, so a far turn in a long session takes seconds.

## Safety

To move an agent's pane, turnr has to type into it: PgUp / PgDn / Ctrl+End, a mouse click on the pinned header and
wheel ticks, sent through herdr's `pane.send_text`. Neither Claude Code nor pi offers another way to scroll their own
view. That is the same thing you would do by hand, but it means a wrong guess about what is in the pane could put keys
somewhere they do not belong. So turnr:

- sends keys only while `herdr agent get` reports an agent of the expected kind in that pane, and only when it is
  `idle` or `working`: never when it is `blocked` (a dialog would take the keys) or when no agent is there (they would
  land in a shell);
- sends no text that could be a command: only scroll keys, mouse events and, if Claude Code's transcript view was left
  open, a single `q` to leave it;
- never edits or deletes session files. It only reads them, and keeps its own cache in `HERDR_PLUGIN_STATE_DIR`.

Read the scripts before installing, as herdr advises for any plugin; they are short.

## Development

```
herdr plugin link /path/to/turnr          # run from a checkout
python3 -m unittest discover -s tests     # tests use made-up session files only
bin/turnr-list <session-id | file.jsonl>  # print a Claude Code session's turns
```

## License

MIT, see [LICENSE](LICENSE). The theme colours and the way the config is read follow [herdr](https://github.com/herdrdev/herdr)
(Apache-2.0), see [NOTICE](NOTICE).
