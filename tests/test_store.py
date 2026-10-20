"""History: one change may touch several files; undo/redo never overwrite a file changed outside the panel."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from montazownia import store
from montazownia.store import ConflictError, History


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="montaz-store-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plan, self.specs = self.tmp / "plan_a.json", self.tmp / "gfx_specs.json"
        self.plan.write_text(json.dumps({"v": 0}, indent=1) + "\n")  # the panel's own formatting
        self.specs.write_text(json.dumps([{"id": "g", "text": "x"}], indent=1) + "\n")
        self.h = History(self.tmp, backup_root=self.tmp / "history")

    def read(self, p):
        return json.loads(p.read_text())

    def test_multi_file_change_undo_redo(self):
        self.h.save("both", {self.plan: {"v": 1}, self.specs: [{"id": "g", "text": "y"}]})
        self.h.undo()
        self.assertEqual((self.read(self.plan), self.read(self.specs)), ({"v": 0}, [{"id": "g", "text": "x"}]))
        self.h.redo()
        self.assertEqual((self.read(self.plan), self.read(self.specs)), ({"v": 1}, [{"id": "g", "text": "y"}]))
        self.assertEqual(len(list(self.h.backup_dir().glob("*.json"))), 6)  # a copy before each of the 3 writes x 2 files (+ original/)

    def test_unchanged_save_is_not_a_change(self):
        self.assertFalse(self.h.save("same", {self.plan: {"v": 0}}).files)
        self.assertIsNone(self.h.state()["undo"])

    def test_conflict_keeps_both_stacks(self):
        self.h.save("one", {self.plan: {"v": 1}})
        self.h.save("two", {self.plan: {"v": 2}})
        self.plan.write_text(json.dumps({"v": "critic --fix"}))  # someone else wrote the plan
        with self.assertRaises(ConflictError):
            self.h.undo()
        self.assertEqual((len(self.h.done), len(self.h.undone)), (2, 0))
        self.assertEqual(self.read(self.plan), {"v": "critic --fix"})

    def test_failed_write_rolls_back_the_first_file(self):
        real = store.atomic_write
        calls = []

        def flaky(p, text):
            calls.append(p)
            if len(calls) == 2:
                raise OSError("disk full")
            real(p, text)

        with mock.patch.object(store, "atomic_write", flaky), self.assertRaises(OSError):
            self.h.save("both", {self.plan: {"v": 1}, self.specs: [{"id": "g", "text": "y"}]})
        self.assertEqual(self.read(self.plan), {"v": 0})
        self.assertEqual(self.h.done, [])

    def test_backups_are_pruned(self):
        with mock.patch.object(store, "KEEP_BACKUPS", 3):
            for v in range(1, 6):
                self.h.save(str(v), {self.plan: {"v": v}})
        d = self.h.backup_dir()
        self.assertEqual(len(list(d.glob("*.json"))), 3)
        self.assertEqual(self.read(d / "original" / self.plan.name), {"v": 0})  # the pre-panel plan survives any number of saves


if __name__ == "__main__":
    unittest.main()
