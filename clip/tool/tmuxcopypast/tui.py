import curses
import locale
import os
from dataclasses import dataclass

from .store import Clip, Store

ACTION_PASTE = "paste"
ACTION_COPY = "copy"

QUICK_KEYS = "123456789"
KEY_ESCAPE = 27
ENTER_KEYS = (curses.KEY_ENTER, 10, 13)
BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127, 8)
ESCAPE_DELAY_MS = "25"
PREVIEW_MIN_LINES = 3
PREVIEW_MAX_LINES = 10
TAB_WIDTH = 4
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400
SOURCE_TAGS = {"ai": "ai", "clip": "clip", "manual": "man"}
# Hotkeys follow physical keys across keyboard layouts.
RU_TO_EN = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбю.ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?",
)

HELP_NORMAL = "Enter/1-9 paste · y copy · x delete · X delete all · / search · q quit"
HELP_FILTER = "type to filter · Enter paste · Esc clear"


@dataclass(frozen=True)
class Row:
    clip: Clip | None

    @property
    def is_separator(self) -> bool:
        return self.clip is None


def format_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < SECONDS_PER_MINUTE:
        return "now"
    if seconds < SECONDS_PER_HOUR:
        return f"{seconds // SECONDS_PER_MINUTE}m"
    if seconds < SECONDS_PER_DAY:
        return f"{seconds // SECONDS_PER_HOUR}h"
    return f"{seconds // SECONDS_PER_DAY}d"


def sanitize(line: str) -> str:
    line = line.replace("\t", " " * TAB_WIDTH)
    return "".join(ch if ch.isprintable() else "·" for ch in line)


def summary(text: str) -> str:
    lines = text.strip("\n").splitlines() or [""]
    head = sanitize(lines[0].strip())
    return f"{head}  (+{len(lines) - 1} lines)" if len(lines) > 1 else head


class Picker:
    def __init__(self, store: Store):
        self.store = store
        self.query = ""
        self.mode = "normal"
        self.cursor = 0
        self.top = 0
        self.rows: list[Row] = []
        self.reload()

    def reload(self) -> None:
        clips = self.store.all()
        if self.query:
            needle = self.query.lower()
            clips = [c for c in clips if needle in c.text.lower() or needle in c.label.lower()]
        actual, history = self.store.split(clips)
        separator = [Row(None)] if actual and history else []
        self.rows = [Row(c) for c in actual] + separator + [Row(c) for c in history]
        self.cursor = min(self.cursor, max(len(self.rows) - 1, 0))
        if self.rows and self.rows[self.cursor].is_separator:
            self.move(1)

    def selectable(self) -> list[int]:
        return [i for i, row in enumerate(self.rows) if not row.is_separator]

    def selected(self) -> Clip | None:
        if not self.rows or self.rows[self.cursor].is_separator:
            return None
        return self.rows[self.cursor].clip

    def move(self, delta: int) -> None:
        indices = self.selectable()
        if not indices:
            self.cursor = 0
            return
        if delta > 0:
            after = [i for i in indices if i > self.cursor]
            self.cursor = after[min(delta, len(after)) - 1] if after else indices[-1]
        elif delta < 0:
            before = [i for i in indices if i < self.cursor]
            self.cursor = before[max(delta, -len(before))] if before else indices[0]
        elif self.cursor not in indices:
            self.move(1)

    def quick_pick(self, digit: int) -> Clip | None:
        indices = self.selectable()
        return self.rows[indices[digit]].clip if digit < len(indices) else None

    def run(self, screen) -> tuple[str, Clip] | None:
        curses.curs_set(0)
        screen.keypad(True)
        curses.use_default_colors()
        while True:
            self.draw(screen)
            key = screen.get_wch()
            code = ord(key) if isinstance(key, str) else key
            result = self.handle_filter(code, key) if self.mode == "filter" else self.handle_normal(code, key, screen)
            if result == "quit":
                return None
            if result:
                return result

    def handle_common(self, code: int, height: int):
        if code in (curses.KEY_UP,):
            self.move(-1)
        elif code in (curses.KEY_DOWN,):
            self.move(1)
        elif code == curses.KEY_PPAGE:
            self.move(-height)
        elif code == curses.KEY_NPAGE:
            self.move(height)
        elif code in ENTER_KEYS:
            clip = self.selected()
            return (ACTION_PASTE, clip) if clip else None
        else:
            return False
        return None

    def handle_normal(self, code: int, key, screen):
        if isinstance(key, str):
            key = key.translate(RU_TO_EN)
        page = max(screen.getmaxyx()[0] // 2, 1)
        handled = self.handle_common(code, page)
        if handled is not False:
            return handled
        if key in ("q",) or code == KEY_ESCAPE:
            return "quit"
        if key == "k":
            self.move(-1)
        elif key == "j":
            self.move(1)
        elif key == "g" or code == curses.KEY_HOME:
            self.cursor = 0
            self.move(0)
        elif key == "G" or code == curses.KEY_END:
            indices = self.selectable()
            self.cursor = indices[-1] if indices else 0
        elif isinstance(key, str) and key in QUICK_KEYS:
            clip = self.quick_pick(QUICK_KEYS.index(key))
            if clip:
                return ACTION_PASTE, clip
        elif key == "y":
            clip = self.selected()
            if clip:
                return ACTION_COPY, clip
        elif key == "x" or code == curses.KEY_DC:
            clip = self.selected()
            if clip:
                self.store.delete(clip.id)
                self.reload()
        elif key == "X":
            if self.confirm(screen, f"Delete all {len(self.selectable())} clips? y/n"):
                self.store.delete_all()
                self.reload()
        elif key == "/":
            self.mode = "filter"
        return None

    def handle_filter(self, code: int, key):
        handled = self.handle_common(code, 1)
        if handled is not False:
            return handled
        if code == KEY_ESCAPE:
            self.query = ""
            self.mode = "normal"
        elif code in BACKSPACE_KEYS:
            self.query = self.query[:-1]
        elif isinstance(key, str) and key.isprintable():
            self.query += key
        else:
            return None
        self.cursor = 0
        self.reload()
        return None

    def confirm(self, screen, question: str) -> bool:
        height, width = screen.getmaxyx()
        self.put(screen, height - 1, 0, question.ljust(width), curses.A_BOLD | curses.A_REVERSE)
        screen.refresh()
        answer = screen.get_wch()
        return isinstance(answer, str) and answer.translate(RU_TO_EN) in ("y", "Y")

    def put(self, screen, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = screen.getmaxyx()
        if 0 <= y < height and x < width:
            try:
                screen.addnstr(y, x, text, width - x - (1 if y == height - 1 else 0), attr)
            except curses.error:
                pass

    def draw(self, screen) -> None:
        screen.erase()
        height, width = screen.getmaxyx()
        clip = self.selected()
        preview_lines = (clip.text.strip("\n").splitlines() if clip else [])[:PREVIEW_MAX_LINES]
        preview_height = max(PREVIEW_MIN_LINES, len(preview_lines))
        list_top = 1
        list_height = max(height - list_top - preview_height - 2, 1)

        total = len(self.selectable())
        title = f" tmuxcopypast · {total} clips" + (f" · filter: {self.query}" if self.query else "")
        self.put(screen, 0, 0, title.ljust(width), curses.A_BOLD)

        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + list_height:
            self.top = self.cursor - list_height + 1

        quick_index = {row_index: n for n, row_index in enumerate(self.selectable()[: len(QUICK_KEYS)])}
        now = self.store.now()
        for offset, row in enumerate(self.rows[self.top : self.top + list_height]):
            index = self.top + offset
            y = list_top + offset
            if row.is_separator:
                self.put(screen, y, 0, " history ".center(width, "─"), curses.A_DIM)
                continue
            item = row.clip
            number = QUICK_KEYS[quick_index[index]] if index in quick_index else " "
            count = f"×{item.paste_count}" if item.paste_count else ""
            label = f"[{item.label}] " if item.label else ""
            tag = SOURCE_TAGS.get(item.source, item.source)
            line = f" {number} {count:>4} {format_age(now - item.batch_at):>4} {tag:<4} {label}{summary(item.text)}"
            self.put(screen, y, 0, line.ljust(width), curses.A_REVERSE if index == self.cursor else 0)

        if not self.rows:
            self.put(screen, list_top, 1, "no clips" if not self.query else "nothing matches", curses.A_DIM)

        preview_top = list_top + list_height
        self.put(screen, preview_top, 0, "─" * width, curses.A_DIM)
        for i, line in enumerate(preview_lines):
            self.put(screen, preview_top + 1 + i, 1, sanitize(line))
        help_text = f"/{self.query}▏  {HELP_FILTER}" if self.mode == "filter" else HELP_NORMAL
        self.put(screen, height - 1, 0, help_text.ljust(width), curses.A_DIM)
        screen.refresh()


def pick(store: Store) -> tuple[str, Clip] | None:
    os.environ.setdefault("ESCDELAY", ESCAPE_DELAY_MS)
    locale.setlocale(locale.LC_ALL, "")
    return curses.wrapper(Picker(store).run)
