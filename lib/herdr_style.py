#!/usr/bin/env python3
"""The look herdr is configured with, so the panel matches it.

Reads this machine's herdr config (HERDR_CONFIG_PATH, else $XDG_CONFIG_HOME/herdr/config.toml, else
~/.config/herdr/config.toml, as herdr finds it) the way herdr does:
  [ui] status_indicators = "dots" | "symbols"
  [theme] name (with herdr's aliases), auto_switch / dark_name, [theme.custom] (and
  [theme.custom.dark]) colour overrides
and the built-in palettes (herdr-themes.json, from herdr v0.9.1's source). Prints one JSON
object: {"indicators", "theme", "colors": {role: "#rrggbb" | an ANSI name | "Reset"}}.
Roles are herdr's own: idle green, working yellow, blocked red, done teal, unknown overlay0;
plus the palette colours used for the rest (green, red, yellow, blue, mauve, dim).
These are settings of herdr's client, the herdr drawing the screen someone looks at: each machine's
dock reads its own machine's. With auto_switch, herdr follows the
terminal's light/dark appearance, which a script can't see: the dark theme is assumed.
"""
import json
import os
import re
import sys

try:
    import tomllib
except ImportError:            # Python < 3.11 (macOS's own python3 is 3.9): read_simple below
    tomllib = None

HERE = os.path.dirname(os.path.realpath(__file__))

# herdr's theme names and aliases (src/config/theme.rs, canonical_theme_name)
ALIASES = {
    "catppuccin-mocha": "catppuccin", "latte": "catppuccin-latte", "light": "catppuccin-latte",
    "tokyonight": "tokyo-night", "tokyo-day": "tokyo-night-day", "tokyonight-day": "tokyo-night-day",
    "gruvbox-dark": "gruvbox", "onedark": "one-dark", "onelight": "one-light", "solarized-dark": "solarized",
    "lotus": "kanagawa-lotus", "rosepine": "rose-pine", "rosepine-dawn": "rose-pine-dawn", "dawn": "rose-pine-dawn",
}
# herdr's named colours (src/config/theme.rs, parse_color) as the palette file spells them
NAMED = {"black": "Black", "red": "Red", "green": "Green", "yellow": "Yellow", "blue": "Blue",
         "magenta": "Magenta", "purple": "Magenta", "cyan": "Cyan", "white": "White", "gray": "Gray", "grey": "Gray",
         "darkgray": "DarkGray", "darkgrey": "DarkGray", "lightred": "LightRed", "lightgreen": "LightGreen",
         "lightyellow": "LightYellow", "lightblue": "LightBlue", "lightmagenta": "LightMagenta", "lightcyan": "LightCyan",
         "reset": "Reset", "default": "Reset", "none": "Reset", "transparent": "Reset"}


def canonical(name):
    n = (name or "catppuccin").lower().replace(" ", "-").replace("_", "-")
    return ALIASES.get(n, n)


def parse_color(value):
    """A colour from herdr's config, read as herdr reads it: #rrggbb, #rgb, rgb(r,g,b) or a name."""
    s = str(value).strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{6})", s)
    if m:
        return "#" + m.group(1)
    m = re.fullmatch(r"#([0-9a-f]{3})", s)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    m = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", s)
    if m:
        return "#%02x%02x%02x" % tuple(min(255, int(x)) for x in m.groups())
    return NAMED.get(s, "Cyan")         # herdr: "unknown color, defaulting to cyan"


def read_simple(text):
    """The part of a TOML file the style needs, without tomllib: [tables] and key = "string" / bool
    lines; arrays, inline tables and [[arrays of tables]] are skipped."""
    cfg, table, depth = {}, None, 0
    for raw in text.splitlines():
        line = raw.split(" #")[0].strip() if not raw.lstrip().startswith("#") else ""
        if depth:                                   # inside a multi-line value
            depth += line.count("[") + line.count("{") - line.count("]") - line.count("}")
            continue
        if not line:
            continue
        m = re.fullmatch(r"\[\s*([A-Za-z0-9_.\-]+)\s*\]", line)
        if m:
            table = cfg
            for part in m.group(1).split("."):
                table = table.setdefault(part, {})
            continue
        if line.startswith("[["):
            table = None
            continue
        m = re.fullmatch(r"([A-Za-z0-9_\-]+)\s*=\s*(.+)", line)
        if not m or table is None:
            continue
        key, val = m.groups()
        if val[0] in "[{":
            depth = val.count("[") + val.count("{") - val.count("]") - val.count("}")
            continue
        if val[0] in "\"'" and val[-1] == val[0] and len(val) >= 2:
            table[key] = val[1:-1]
        elif val in ("true", "false"):
            table[key] = val == "true"
    return cfg


def config_path():
    if os.environ.get("HERDR_CONFIG_PATH"):
        return os.environ["HERDR_CONFIG_PATH"]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "herdr", "config.toml")


def style():
    path = config_path()
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = f.read()
            cfg = tomllib.loads(data.decode()) if tomllib else read_simple(data.decode())
        except Exception:
            cfg = {}                   # herdr itself falls back to its defaults on a config it can't read
    with open(os.path.join(HERE, "herdr-themes.json")) as f:
        themes = json.load(f)["themes"]
    theme = cfg.get("theme", {}) or {}
    auto = bool(theme.get("auto_switch"))
    name = canonical((auto and theme.get("dark_name")) or theme.get("name"))
    if name not in themes:
        name = "catppuccin"
    pal = dict(themes[name])
    custom = theme.get("custom", {}) or {}
    for layer in (custom, custom.get("dark", {}) if auto else {}):
        for k, v in layer.items():
            if k in pal and not isinstance(v, dict):
                pal[k] = parse_color(v)
    ind = (cfg.get("ui", {}) or {}).get("status_indicators", "dots")
    return {
        "indicators": ind if ind in ("dots", "symbols") else "dots",
        "theme": name,
        "palette": pal,        # turnr: the whole palette (surfaces, text, overlays), not only the roles below
        "colors": {
            "idle": pal["green"], "working": pal["yellow"], "blocked": pal["red"], "done": pal["teal"],
            "unknown": pal["overlay0"], "asleep": pal["overlay0"],
            "green": pal["green"], "red": pal["red"], "yellow": pal["yellow"],
            # dim text (hints, labels, the legend): herdr's terminal theme gives Gray,
            # ANSI light grey, near-white in many terminal themes, and DarkGray is too dark to read;
            # a fixed light-mid grey (#b0b0b0) reads well on both
            "blue": pal["blue"], "mauve": pal["mauve"], "dim": "#b0b0b0" if pal["overlay0"] == "Gray" else pal["overlay0"],
            "orange": pal.get("peach") or pal["yellow"],   # a busy machine's load (75–89%)
            "accent": pal.get("accent") or pal["blue"],    # the machine the dock runs on, as herdr marks what's active
        },
    }


if __name__ == "__main__":
    json.dump(style(), sys.stdout)
    sys.stdout.write("\n")
