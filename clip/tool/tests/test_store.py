import tempfile
import unittest
from pathlib import Path

from tmuxcopypast.store import SOURCE_AI, SOURCE_CLIP, Store

MINUTE = 60


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.value = start

    def __call__(self) -> float:
        return self.value


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.store = Store(Path(self.tmp.name) / "clips.db", now=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def texts(self):
        return [clip.text for clip in self.store.all()]

    def test_batch_keeps_given_order_and_newer_batch_goes_first(self):
        self.store.add_batch([("old", "")], SOURCE_AI)
        self.clock.value += MINUTE
        self.store.add_batch([("cmd1", "a"), ("cmd2", "b"), ("cmd3", "c")], SOURCE_AI)
        self.assertEqual(self.texts(), ["cmd1", "cmd2", "cmd3", "old"])
        self.assertEqual([c.label for c in self.store.all()][:3], ["a", "b", "c"])

    def test_readding_same_text_moves_it_up_without_duplicate(self):
        self.store.add_batch([("same", "")], SOURCE_AI)
        self.clock.value += MINUTE
        self.store.add_batch([("other", "")], SOURCE_AI)
        self.clock.value += MINUTE
        self.store.add_batch([("same", "")], SOURCE_AI)
        self.assertEqual(self.texts(), ["same", "other"])

    def test_blank_text_is_ignored(self):
        self.assertEqual(self.store.add_batch([("  \n", "")], SOURCE_AI), [])
        self.assertEqual(self.texts(), [])

    def test_split_by_actual_window(self):
        self.store.add_batch([("history", "")], SOURCE_AI)
        self.clock.value += 2 * 60 * MINUTE
        self.store.add_batch([("fresh", "")], SOURCE_AI)
        actual, history = self.store.split(self.store.all(), minutes=60)
        self.assertEqual([c.text for c in actual], ["fresh"])
        self.assertEqual([c.text for c in history], ["history"])

    def test_buffer_imported_once_with_its_own_time(self):
        created = int(self.clock.value) - 10 * MINUTE
        self.assertTrue(self.store.import_buffer("buffer1", created, "copied"))
        self.assertFalse(self.store.import_buffer("buffer1", created, "copied"))
        clip = self.store.all()[0]
        self.assertEqual((clip.source, clip.batch_at), (SOURCE_CLIP, float(created)))

    def test_stale_buffer_does_not_bump_newer_clip(self):
        self.store.add_batch([("text", "label")], SOURCE_AI)
        self.store.import_buffer("buffer7", int(self.clock.value) - 3600, "text")
        clip = self.store.all()[0]
        self.assertEqual((clip.source, clip.label, clip.batch_at), (SOURCE_AI, "label", self.clock.value))

    def test_paste_counter_delete_and_clear(self):
        first, second = self.store.add_batch([("one", ""), ("two", "")], SOURCE_AI)
        self.store.mark_pasted(first)
        self.store.mark_pasted(first)
        self.assertEqual(self.store.get(first).paste_count, 2)
        self.store.delete(first)
        self.assertEqual(self.texts(), ["two"])
        self.assertEqual(self.store.delete_all(), 1)
        self.assertEqual(self.texts(), [])

    def test_cleared_buffer_is_not_reimported(self):
        self.store.import_buffer("buffer1", 1, "copied")
        self.store.delete_all()
        self.assertFalse(self.store.import_buffer("buffer1", 1, "copied"))
        self.assertEqual(self.texts(), [])


if __name__ == "__main__":
    unittest.main()
