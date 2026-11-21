from pathlib import Path
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.security import Secrets
from backend.store import Store


class StorageMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.secrets = Secrets(self.directory)
        self.store = Store(self.directory, self.secrets)
        self.addCleanup(lambda: self.store and self.store.close())

    def test_summary_is_private_and_counts_persist_after_reopen(self):
        self.store.save_account("offline-account", "offline-username", "offline-password")
        summary = self.store.storage_summary()
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["journal_mode"], "wal")
        self.assertEqual(summary["records"]["accounts"], 1)
        self.assertGreater(summary["database_bytes"], 0)
        self.assertNotIn("offline-", str(summary))
        self.assertNotIn(self.secrets.token, str(summary))
        self.store.close()
        self.store = Store(self.directory, self.secrets)
        self.assertEqual(self.store.storage_summary()["records"]["accounts"], 1)

    def test_summary_tolerates_a_wal_file_disappearing(self):
        original = Path.stat

        def stat(path, *args, **kwargs):
            if str(path).endswith("-wal"):
                raise FileNotFoundError(path)
            return original(path, *args, **kwargs)

        with patch.object(Path, "stat", stat):
            self.assertEqual(self.store.storage_summary()["status"], "ok")

    def test_newer_schema_is_rejected_and_not_reset(self):
        self.store.connection.execute("PRAGMA user_version=99")
        self.store.close()
        self.store = None
        with self.assertRaisesRegex(ValueError, "newer backend"):
            Store(self.directory, self.secrets)
        with closing(sqlite3.connect(self.directory / "service.sqlite3")) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_a_version_one_answer_table_is_upgraded_additively(self):
        directory = self.directory / "legacy"
        directory.mkdir()
        secrets = Secrets(directory)
        with closing(sqlite3.connect(directory / "service.sqlite3")) as db:
            db.executescript("""
                PRAGMA user_version=1;
                CREATE TABLE answers (
                    id TEXT PRIMARY KEY, cache_key TEXT UNIQUE NOT NULL, question TEXT NOT NULL,
                    state TEXT NOT NULL, answer TEXT, error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
            """)
            db.execute(
                "INSERT INTO answers VALUES (?,?,?,'done',?,'',1,1)",
                ("legacy-id", "legacy-key", json.dumps({
                    "title": "Question", "type": "single", "options": "A. 1\nB. 2", "course_id": "1",
                }), '{"Answer":["B"]}'),
            )
            db.commit()
        store = Store(directory, secrets)
        try:
            self.assertEqual(store.stats()["question_bank"]["entries"], 1)
            with store.transaction() as db:
                row = db.execute("SELECT * FROM answers WHERE id='legacy-id'").fetchone()
                self.assertEqual(row["cache_key"], "legacy-key")
                self.assertEqual(row["bank_index_version"], 1)
                self.assertEqual(json.loads(row["answer"]), {"Answer": ["B. 2"]})
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
