import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import Board, PALETTE


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.board = Board(str(Path(self.dir.name) / "test.db"))
        self.a, self.a_key = self.board.create_participant()
        self.b, self.b_key = self.board.create_participant()
        self.c, self.c_key = self.board.create_participant()
        self.accent = PALETTE["Ivory"]

    def tearDown(self):
        self.dir.cleanup()

    def test_no_default_people_and_private_keys(self):
        self.assertEqual(self.board.members(), [1, 2, 3])
        self.assertEqual(self.board.login(self.a_key), self.a)
        self.assertEqual(self.board.login(self.b_key), self.b)
        self.assertIsNone(self.board.login(self.a_key + "x"))
        with self.board.connect() as conn:
            contents = conn.execute("SELECT key_hash FROM participants WHERE id=1").fetchone()[0]
        self.assertNotEqual(contents, self.a_key)

    def test_private_message_is_not_discoverable_or_exported(self):
        p = self.board.post(self.a, self.b, "Letter", "Private title", "private story", self.accent)
        self.assertEqual([r['id'] for r in self.board.timeline(self.a)], [p])
        self.assertEqual([r['id'] for r in self.board.timeline(self.b)], [p])
        self.assertEqual(self.board.timeline(self.c), [])
        self.assertEqual(self.board.timeline(self.c, search='story'), [])
        self.assertNotIn('private story', self.board.export_visible(self.c))
        self.assertIsNone(self.board.find_root(p, self.c))
        with self.assertRaises(PermissionError):
            self.board.post(self.c, None, "Reply", "", "can't reply", self.accent, parent_id=p)

    def test_group_messages_and_per_person_read(self):
        p = self.board.post(self.a, None, "Note", "Hi", "room text", self.accent)
        self.assertEqual(self.board.count_unread(self.a), 0)
        self.assertEqual(self.board.count_unread(self.b), 1)
        self.assertEqual(self.board.count_unread(self.c), 1)
        self.board.mark_seen(p, self.b)
        self.assertEqual(self.board.count_unread(self.b), 0)
        self.assertEqual(self.board.count_unread(self.c), 1)
        self.assertEqual(self.board.read_status(p, self.a), "Read by 1")
        self.assertEqual(len(self.board.timeline(self.c, "Unread")), 1)

    def test_replies_keep_audience_and_are_unread_independently(self):
        root = self.board.post(self.a, self.b, "Note", "A-B", "hello B", self.accent)
        reply = self.board.post(self.b, None, "Reply", "", "back to A", self.accent, root)
        self.assertEqual(self.board.replies(root, self.c), [])
        self.assertEqual(len(self.board.replies(root, self.a)), 1)
        with self.board.connect() as conn:
            row = conn.execute("SELECT recipient_id FROM messages WHERE id=?", (reply,)).fetchone()
        self.assertEqual(row[0], self.a)
        self.assertEqual(self.board.count_unread(self.a), 1)
        self.board.mark_seen(reply, self.a)
        self.assertEqual(self.board.count_unread(self.a), 0)
        self.assertEqual(self.board.read_status(reply, self.b), 'Read')

    def test_group_reply_is_visible_to_everyone(self):
        root = self.board.post(self.a, None, "Note", "Group", "hello", self.accent)
        reply = self.board.post(self.b, self.a, "Reply", "", "all see", self.accent, root)
        self.assertEqual(self.board.replies(root, self.c)[0]['id'], reply)
        self.assertEqual(self.board.count_unread(self.c), 2)

    def test_search_bookmarks_and_removal(self):
        p = self.board.post(self.a, self.b, "Letter", "Thinking", "deep ocean", self.accent)
        self.assertEqual(self.board.timeline(self.b, search="ocean")[0]["id"], p)
        self.board.toggle_saved(p, self.b)
        self.assertEqual(self.board.timeline(self.b, "Saved")[0]["id"], p)
        self.assertEqual(self.board.timeline(self.c, "Saved"), [])
        self.assertFalse(self.board.remove_own(p, self.c))
        self.assertTrue(self.board.remove_own(p, self.a))
        self.assertEqual(self.board.timeline(self.b), [])
        self.assertNotIn('deep ocean', self.board.export_visible(self.b))

    def test_delete_root_preserves_replies_without_leaking_content(self):
        p = self.board.post(self.a, None, "Note", "original", "first text", self.accent)
        self.board.post(self.b, None, "Reply", "", "still there", self.accent, p)
        self.board.remove_own(p, self.a)
        roots = self.board.timeline(self.c)
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]['reply_count'], 1)
        self.assertEqual(roots[0]['body'], '')
        self.assertEqual(self.board.replies(p, self.c)[0]['body'], "still there")

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.board.post(self.a, self.a, "Note", "title", "test", self.accent)
        with self.assertRaises(ValueError):
            self.board.post(self.a, None, "Note", "title", "", self.accent)
        with self.assertRaises(ValueError):
            self.board.post(self.a, None, "Note", "title", "test", "red")


if __name__ == '__main__':
    unittest.main()
