import os
import subprocess

from .store import Store

TMUX_BIN = os.environ.get("TCP_TMUX", "tmux")
# Buffers this tool creates for itself; never imported back as clips.
OWN_BUFFER_PREFIX = "tcp-"
PASTE_BUFFER = OWN_BUFFER_PREFIX + "paste"
CLIPBOARD_BUFFER = OWN_BUFFER_PREFIX + "clipboard"


def run(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        [TMUX_BIN, *args],
        input=stdin.encode("utf-8", "surrogateescape") if stdin is not None else None,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8", "replace")


def list_buffers() -> list[tuple[str, int]]:
    out = run("list-buffers", "-F", "#{buffer_name}\t#{buffer_created}")
    buffers = []
    for line in out.splitlines():
        name, _, created = line.partition("\t")
        if name and created.isdigit():
            buffers.append((name, int(created)))
    return buffers


def sync(store: Store) -> int:
    imported = 0
    for name, created in list_buffers():
        if name.startswith(OWN_BUFFER_PREFIX) or store.has_seen_buffer(name, created):
            continue
        try:
            text = run("show-buffer", "-b", name)
        except subprocess.CalledProcessError:
            continue
        if store.import_buffer(name, created, text):
            imported += 1
    return imported


# display-popup does not expand formats in its command, so the popup asks tmux which pane/client it is over.
def current_target() -> tuple[str, str]:
    pane, _, client = run("display-message", "-p", "#{pane_id}\t#{client_name}").strip().partition("\t")
    return pane, client


def paste(text: str, pane: str) -> None:
    run("load-buffer", "-b", PASTE_BUFFER, "-", stdin=text)
    run("paste-buffer", "-p", "-d", "-b", PASTE_BUFFER, "-t", pane)


# -w needs an explicit client: a command run from the popup has none, so OSC52 would go nowhere.
def copy_to_clipboard(text: str, client: str) -> None:
    run("load-buffer", "-w", "-t", client, "-b", CLIPBOARD_BUFFER, "-", stdin=text)
    run("delete-buffer", "-b", CLIPBOARD_BUFFER)


def show_message(text: str) -> None:
    run("display-message", "-d", "5000", text)
