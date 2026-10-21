"""SQLite storage for Between Us. No Streamlit dependency."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

PALETTE = {
    "Ivory": "#F6F1E8",
    "Sage": "#E6EEE4",
    "Sky": "#E7EEF7",
    "Blush": "#F5E8E7",
    "Lilac": "#EEEAF5",
}
MAX_BODY_LENGTH = 100_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_key() -> str:
    return secrets.token_urlsafe(32)


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class Board:
    def __init__(self, path: str = "between_us.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 10000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_hash TEXT NOT NULL UNIQUE,
                    joined_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author_id INTEGER NOT NULL REFERENCES participants(id),
                    recipient_id INTEGER REFERENCES participants(id),
                    parent_id INTEGER REFERENCES messages(id),
                    kind TEXT NOT NULL CHECK(kind IN ('Note','Letter','Reply')),
                    title TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL,
                    accent TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS message_reads (
                    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                    read_at TEXT NOT NULL,
                    PRIMARY KEY (message_id, participant_id)
                );
                CREATE TABLE IF NOT EXISTS saved (
                    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                    PRIMARY KEY (message_id, participant_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_id);
                CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient_id);
                CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
                CREATE INDEX IF NOT EXISTS idx_reads_participant ON message_reads(participant_id);
            """)

    def create_participant(self) -> tuple[int, str]:
        private_key = make_key()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO participants(key_hash, joined_at) VALUES (?, ?)",
                (key_digest(private_key), utc_now()),
            )
            return cursor.lastrowid, private_key

    def login(self, private_key: str) -> int | None:
        if not private_key or len(private_key) > 300:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM participants WHERE key_hash = ?",
                (key_digest(private_key.strip()),),
            ).fetchone()
            return row["id"] if row else None

    def members(self) -> list[int]:
        with self.connect() as conn:
            return [r["id"] for r in conn.execute("SELECT id FROM participants ORDER BY id")]

    def _visible(self, member_id: int) -> str:
        return "(recipient_id IS NULL OR recipient_id = ? OR author_id = ?)"

    def count_unread(self, member_id: int) -> int:
        with self.connect() as conn:
            return conn.execute("""
                SELECT COUNT(*) FROM messages m
                WHERE m.deleted_at IS NULL AND m.author_id != ?
                AND (m.recipient_id IS NULL OR m.recipient_id = ?)
                AND NOT EXISTS (
                    SELECT 1 FROM message_reads x
                    WHERE x.message_id = m.id AND x.participant_id = ?
                )
            """, (member_id, member_id, member_id)).fetchone()[0]

    def post(self, member_id: int, recipient_id: int | None, kind: str,
             title: str, body: str, accent: str, parent_id: int | None = None) -> int:
        title, body = title.strip(), body.strip()
        if kind not in ("Note", "Letter", "Reply"):
            raise ValueError("Invalid post type")
        if accent not in PALETTE.values():
            raise ValueError("Invalid accent color")
        if not body or len(body) > MAX_BODY_LENGTH or len(title) > 140:
            raise ValueError("Message required (up to 100,000 characters), title up to 140")
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM participants WHERE id = ?", (member_id,)).fetchone():
                raise PermissionError("Unknown participant")
            if parent_id is not None:
                root = conn.execute(
                    "SELECT * FROM messages WHERE id = ? AND parent_id IS NULL AND deleted_at IS NULL",
                    (parent_id,),
                ).fetchone()
                if not root or not self.can_view_row(root, member_id):
                    raise PermissionError("Thread not available")
                kind, title, accent = "Reply", "", root["accent"]
                if root["recipient_id"] is None:
                    recipient_id = None
                else:
                    recipient_id = (root["recipient_id"]
                                    if root["author_id"] == member_id
                                    else root["author_id"])
            elif recipient_id is not None:
                if recipient_id == member_id or not conn.execute(
                    "SELECT 1 FROM participants WHERE id = ?", (recipient_id,)
                ).fetchone():
                    raise ValueError("Invalid recipient")
            cursor = conn.execute("""
                INSERT INTO messages
                (author_id,recipient_id,parent_id,kind,title,body,accent,created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (member_id, recipient_id, parent_id, kind, title, body, accent, utc_now()))
            return cursor.lastrowid

    @staticmethod
    def can_view_row(row: sqlite3.Row, member_id: int) -> bool:
        return row["recipient_id"] is None or row["recipient_id"] == member_id or row["author_id"] == member_id

    def find_root(self, root_id: int, member_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ? AND parent_id IS NULL", (root_id,)
            ).fetchone()
            return row if row and self.can_view_row(row, member_id) else None

    def timeline(self, member_id: int, view: str = "All", search: str = "", limit: int = 70) -> list[sqlite3.Row]:
        if view not in ("All", "Unread", "Mine", "Saved"):
            raise ValueError("Invalid view")
        filters = ["m.parent_id IS NULL",
                   "(m.recipient_id IS NULL OR m.recipient_id = ? OR m.author_id = ?)",
                   "(m.deleted_at IS NULL OR EXISTS (SELECT 1 FROM messages q WHERE q.parent_id=m.id AND q.deleted_at IS NULL))"]
        values: list = [member_id, member_id]
        if view == "Mine":
            filters.append("m.author_id = ?")
            values.append(member_id)
        elif view == "Saved":
            filters.append("EXISTS (SELECT 1 FROM saved s WHERE s.message_id=m.id AND s.participant_id=?)")
            values.append(member_id)
        elif view == "Unread":
            filters.append("EXISTS (SELECT 1 FROM messages u WHERE (u.id=m.id OR u.parent_id=m.id) AND u.deleted_at IS NULL AND u.author_id != ? AND (u.recipient_id IS NULL OR u.recipient_id=?) AND NOT EXISTS (SELECT 1 FROM message_reads mr WHERE mr.message_id=u.id AND mr.participant_id=?))")
            values.extend((member_id, member_id, member_id))
        if search.strip():
            pattern = "%" + search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            filters.append("""EXISTS (
                SELECT 1 FROM messages x
                WHERE (x.id=m.id OR x.parent_id=m.id) AND x.deleted_at IS NULL
                AND (x.title LIKE ? ESCAPE '\\' OR x.body LIKE ? ESCAPE '\\')
            )""")
            values.extend((pattern, pattern))
        values.append(min(max(1, limit), 200))
        sql = f"""
            SELECT m.*,
              (SELECT COUNT(*) FROM messages r WHERE r.parent_id=m.id AND r.deleted_at IS NULL) AS reply_count,
              (SELECT MAX(r.created_at) FROM messages r WHERE r.parent_id=m.id) AS reply_activity,
              EXISTS(SELECT 1 FROM saved s WHERE s.message_id=m.id AND s.participant_id=?) AS is_saved
            FROM messages m WHERE {' AND '.join(filters)}
            ORDER BY COALESCE((SELECT MAX(r.created_at) FROM messages r WHERE r.parent_id=m.id),m.created_at) DESC, m.id DESC
            LIMIT ?
        """
        # The first placeholder is in SELECT, before the WHERE placeholders.
        with self.connect() as conn:
            return conn.execute(sql, [member_id, *values]).fetchall()

    def replies(self, root_id: int, member_id: int) -> list[sqlite3.Row]:
        root = self.find_root(root_id, member_id)
        if root is None:
            return []
        with self.connect() as conn:
            return conn.execute("""
                SELECT * FROM messages WHERE parent_id = ?
                AND (recipient_id IS NULL OR recipient_id = ? OR author_id = ?)
                ORDER BY created_at ASC, id ASC
            """, (root_id, member_id, member_id)).fetchall()

    def is_unread(self, message_id: int, member_id: int) -> bool:
        with self.connect() as conn:
            return bool(conn.execute("""
                SELECT 1 FROM messages m WHERE m.id=? AND m.deleted_at IS NULL
                  AND m.author_id != ? AND (m.recipient_id IS NULL OR m.recipient_id=?)
                  AND NOT EXISTS (SELECT 1 FROM message_reads r WHERE r.message_id=m.id AND r.participant_id=?)
            """, (message_id, member_id, member_id, member_id)).fetchone())

    def mark_seen(self, message_id: int, member_id: int) -> None:
        with self.connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO message_reads(message_id,participant_id,read_at)
                SELECT id, ?, ? FROM messages WHERE id=? AND deleted_at IS NULL
                AND author_id != ? AND (recipient_id IS NULL OR recipient_id=?)
            """, (member_id, utc_now(), message_id, member_id, member_id))

    def read_status(self, message_id: int, member_id: int) -> str:
        with self.connect() as conn:
            m = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if not m or m["author_id"] != member_id:
                return ""
            if m["recipient_id"] is not None:
                seen = conn.execute("SELECT 1 FROM message_reads WHERE message_id=? AND participant_id=?",
                                    (message_id, m["recipient_id"])).fetchone()
                return "Read" if seen else "Not yet read"
            count = conn.execute("SELECT COUNT(*) FROM message_reads WHERE message_id=?", (message_id,)).fetchone()[0]
            return f"Read by {count}" if count else "Shared with the room"

    def toggle_saved(self, root_id: int, member_id: int) -> None:
        root = self.find_root(root_id, member_id)
        if root is None:
            raise PermissionError("Cannot save this post")
        with self.connect() as conn:
            exists = conn.execute("SELECT 1 FROM saved WHERE message_id=? AND participant_id=?",
                                  (root_id, member_id)).fetchone()
            if exists:
                conn.execute("DELETE FROM saved WHERE message_id=? AND participant_id=?", (root_id, member_id))
            else:
                conn.execute("INSERT INTO saved(message_id,participant_id) VALUES (?,?)", (root_id, member_id))

    def remove_own(self, message_id: int, member_id: int) -> bool:
        # Soft-removal preserves thread integrity; content is scrubbed immediately.
        with self.connect() as conn:
            cur = conn.execute("""
                UPDATE messages SET title='', body='', deleted_at=?
                WHERE id=? AND author_id=? AND deleted_at IS NULL
            """, (utc_now(), message_id, member_id))
            return cur.rowcount > 0

    def export_visible(self, member_id: int) -> str:
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT * FROM messages WHERE
                (recipient_id IS NULL OR recipient_id=? OR author_id=?)
                ORDER BY created_at ASC, id ASC
            """, (member_id, member_id)).fetchall()
        parts = []
        for r in rows:
            to = "Everyone" if r["recipient_id"] is None else f"Member {r['recipient_id']:02d}"
            by = f"Member {r['author_id']:02d}"
            text = "[Message removed]" if r["deleted_at"] else r["body"]
            parts.append(f"[{r['created_at']}] {r['kind']} · {by} → {to}\n"
                         f"{r['title']}\n{text}")
        return "\n\n---\n\n".join(parts) if parts else "No messages yet."
