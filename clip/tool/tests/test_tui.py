import tempfile
import unittest
from pathlib import Path

from tmuxcopypast.store import SOURCE_AI, Store
from tmuxcopypast.tui import ACTION_COPY, Picker


class FakeScreen:
    def __init__(self, answer: str = ""):
        self.answer = answer

    def getmaxyx(self):
        return 24, 80

    def get_wch(self):
        return self.answer

    def addnstr(self, *args):
        pass

    def refresh(self):
        pass


class RussianLayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "clips.db")
        self.store.add_batch([("one", ""), ("two", ""), ("three", "")], SOURCE_AI)
        self.picker = Picker(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def press(self, key: str, screen: FakeScreen | None = None):
        return self.picker.handle_normal(ord(key), key, screen or FakeScreen())

    def test_movement_copy_and_quit(self):
        self.press("о")
        self.assertEqual(self.picker.selected().text, "two")
        self.press("л")
        self.assertEqual(self.picker.selected().text, "one")
        self.assertEqual(self.press("н"), (ACTION_COPY, self.picker.selected()))
        self.assertEqual(self.press("й"), "quit")

    def test_delete_and_confirmed_delete_all(self):
        self.press("ч")
        self.assertEqual(len(self.store.all()), 2)
        self.press("Ч", FakeScreen("н"))
        self.assertEqual(self.store.all(), [])

    def test_search_key(self):
        self.press(".")
        self.assertEqual(self.picker.mode, "filter")


if __name__ == "__main__":
    unittest.main()
