import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from . import tmux
from .store import SOURCE_AI, SOURCE_CLIP, SOURCE_MANUAL, Store


def cmd_add(store: Store, args) -> int:
    texts = args.text if args.text else [sys.stdin.read()]
    labels = args.label or []

    def label_for(index: int) -> str:
        if len(labels) == 1:
            return labels[0]
        return labels[index] if index < len(labels) else ""

    items = [(text, label_for(i)) for i, text in enumerate(texts)]
    ids = store.add_batch(items, args.source)
    print(" ".join(str(i) for i in ids))
    return 0 if ids else 1


def cmd_sync(store: Store, _args) -> int:
    try:
        tmux.sync(store)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 1
    return 0


def cmd_pick(store: Store, args) -> int:
    from .tui import ACTION_COPY, ACTION_PASTE, pick

    cmd_sync(store, args)
    action = pick(store)
    if not action:
        return 0
    kind, clip = action
    try:
        pane, client = tmux.current_target()
        if kind == ACTION_PASTE:
            explicit_pane = args.pane if args.pane and args.pane.startswith("%") else None
            tmux.paste(clip.text, explicit_pane or pane)
        elif kind == ACTION_COPY:
            tmux.copy_to_clipboard(clip.text, client)
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        report_error(f"{kind} failed", error)
        return 1
    store.mark_pasted(clip.id)
    return 0


def error_log_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(state_home) / "tmuxcopypast" / "error.log"


def report_error(what: str, error: Exception) -> None:
    stderr = getattr(error, "stderr", b"") or b""
    detail = stderr.decode("utf-8", "replace").strip() or str(error)
    path = error_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as log:
        log.write(f"{datetime.now().isoformat(timespec='seconds')} {what}: {detail}\n{traceback.format_exc()}\n")
    try:
        tmux.show_message(f"tcp: {what}: {detail} (see {path})")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def cmd_list(store: Store, args) -> int:
    actual, history = store.split(store.all())
    if args.json:
        print(json.dumps({
            "actual": [clip.__dict__ for clip in actual],
            "history": [clip.__dict__ for clip in history],
        }, ensure_ascii=False))
        return 0
    for group, clips in (("actual", actual), ("history", history)):
        print(f"## {group}")
        for clip in clips:
            first_line = clip.text.strip().splitlines()[0] if clip.text.strip() else ""
            print(f"{clip.id}\t×{clip.paste_count}\t{clip.source}\t{clip.label}\t{first_line[:100]}")
    return 0


def cmd_rm(store: Store, args) -> int:
    for clip_id in args.id:
        store.delete(clip_id)
    return 0


def cmd_clear(store: Store, _args) -> int:
    print(store.delete_all())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tcp", description="tmux copy-paste list (F9)")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="add clips; each TEXT is one clip, no TEXT = read one clip from stdin")
    add.add_argument("-s", "--source", default=SOURCE_MANUAL, choices=[SOURCE_AI, SOURCE_CLIP, SOURCE_MANUAL])
    add.add_argument("-l", "--label", action="append",
                     help="label; repeat to label clips in order, a single -l labels all")
    add.add_argument("text", nargs="*")
    add.set_defaults(func=cmd_add)

    pick = sub.add_parser("pick", help="interactive list (run inside tmux display-popup)")
    pick.add_argument("--pane", help="target pane id for paste (default: current pane)")
    pick.set_defaults(func=cmd_pick)

    sub.add_parser("sync", help="import new tmux buffers (OSC52 / copy-mode)").set_defaults(func=cmd_sync)

    lst = sub.add_parser("list", help="print clips")
    lst.add_argument("--json", action="store_true")
    lst.set_defaults(func=cmd_list)

    rm = sub.add_parser("rm", help="delete clips by id")
    rm.add_argument("id", type=int, nargs="+")
    rm.set_defaults(func=cmd_rm)

    sub.add_parser("clear", help="delete all clips").set_defaults(func=cmd_clear)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(Store(), args)
