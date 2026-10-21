"""Between Us — a single-file, open shared notes board.

Run: streamlit run app-1.py
There are NO accounts, access codes, identities, private messages, or return keys.
Every published message is visible to anyone who can access the board.
"""
from __future__ import annotations

import html
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import streamlit as st


# ─── CONFIGURATION ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Between Us | Notes & Letters",
    page_icon="✉️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BOARD_DB_PATH = os.environ.get("BETWEEN_US_DB_PATH", "between_us_open.db")
MAX_MESSAGE_CHARS = 100_000
MAX_TITLE_CHARS = 140
PAGE_SIZE = 25
REFRESH_INTERVAL = "15s"
COLORS = {
    "Sage": "#758F7E",
    "Sand": "#B49170",
    "Lilac": "#9D8BAF",
    "Sky": "#849EBC",
    "Blush": "#BC8F93",
}


# ─── DATABASE: ALL OPERATIONS ARE IN THIS ONE FILE ────────────────────────────
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db_connection(db_path: str = BOARD_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Use short-lived SQLite connections to avoid shared-connection thread issues."""
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = BOARD_DB_PATH) -> None:
    parent = Path(db_path).parent
    parent.mkdir(parents=True, exist_ok=True)
    with db_connection(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('Note', 'Letter')),
                title TEXT NOT NULL,
                body TEXT NOT NULL CHECK(length(trim(body)) > 0),
                accent TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                body TEXT NOT NULL CHECK(length(trim(body)) > 0),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_posts_new ON posts(id DESC);
            CREATE INDEX IF NOT EXISTS idx_replies_post ON replies(post_id, id);
        """)


def add_post(kind: str, title: str, body: str, accent: str,
             db_path: str = BOARD_DB_PATH) -> int:
    kind, title, body = kind.strip(), title.strip(), body.strip()
    if kind not in ("Note", "Letter"):
        raise ValueError("Choose Note or Letter.")
    if not body:
        raise ValueError("Write something before publishing.")
    if len(body) > MAX_MESSAGE_CHARS or len(title) > MAX_TITLE_CHARS:
        raise ValueError("Your writing exceeds the allowed length.")
    if accent not in COLORS.values():
        raise ValueError("Choose one of the available paper tones.")
    with db_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO posts(kind, title, body, accent, created_at) VALUES (?,?,?,?,?)",
            (kind, title or ("A letter" if kind == "Letter" else "A note"), body,
             accent, utc_now()),
        )
        return int(cur.lastrowid)


def add_reply(post_id: int, body: str, db_path: str = BOARD_DB_PATH) -> int:
    body = body.strip()
    if not body:
        raise ValueError("Write something before replying.")
    if len(body) > MAX_MESSAGE_CHARS:
        raise ValueError("This reply exceeds the allowed length.")
    with db_connection(db_path) as conn:
        cur = conn.execute("SELECT id FROM posts WHERE id=?", (post_id,))
        if cur.fetchone() is None:
            raise ValueError("This post is no longer available.")
        result = conn.execute(
            "INSERT INTO replies(post_id, body, created_at) VALUES(?,?,?)",
            (post_id, body, utc_now()),
        )
        return int(result.lastrowid)


def board_counts(db_path: str = BOARD_DB_PATH) -> tuple[int, int, int]:
    with db_connection(db_path) as conn:
        posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        letters = conn.execute("SELECT COUNT(*) FROM posts WHERE kind='Letter'").fetchone()[0]
        replies = conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0]
        return posts, letters, replies


def list_posts(query: str = "", kind: str = "All", limit: int = PAGE_SIZE,
               db_path: str = BOARD_DB_PATH) -> list[sqlite3.Row]:
    """Search post bodies, titles and replies; return latest matching root posts."""
    query = query.strip()[:200]
    limit = max(1, min(int(limit), 250))
    filters = []
    args: list[object] = []
    if kind in ("Note", "Letter"):
        filters.append("p.kind = ?")
        args.append(kind)
    if query:
        # Escape % and _ so user input is treated literally, not as a SQL wildcard.
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%") .replace("_", "\\_") + "%"
        filters.append("""(p.title LIKE ? ESCAPE '\\' OR p.body LIKE ? ESCAPE '\\'
                       OR EXISTS (SELECT 1 FROM replies r WHERE r.post_id=p.id
                       AND r.body LIKE ? ESCAPE '\\'))""")
        args.extend([pattern, pattern, pattern])
    where = " WHERE " + " AND ".join(filters) if filters else ""
    with db_connection(db_path) as conn:
        return conn.execute(
            """SELECT p.id, p.kind, p.title, p.body, p.accent, p.created_at,
                      (SELECT COUNT(*) FROM replies r WHERE r.post_id=p.id) AS reply_count
               FROM posts p""" + where + " ORDER BY p.id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()


def list_replies(post_id: int, db_path: str = BOARD_DB_PATH) -> list[sqlite3.Row]:
    with db_connection(db_path) as conn:
        return conn.execute(
            "SELECT id, body, created_at FROM replies WHERE post_id=? ORDER BY id ASC",
            (post_id,),
        ).fetchall()


def export_all(db_path: str = BOARD_DB_PATH) -> str:
    """Export everything: this board has no private messages or accounts."""
    with db_connection(db_path) as conn:
        posts = conn.execute(
            "SELECT id, kind, title, body, created_at FROM posts ORDER BY id ASC"
        ).fetchall()
        replies = conn.execute(
            "SELECT post_id, body, created_at FROM replies ORDER BY id ASC"
        ).fetchall()
    grouped: dict[int, list[sqlite3.Row]] = {}
    for reply in replies:
        grouped.setdefault(reply["post_id"], []).append(reply)
    blocks = ["BETWEEN US — FULL SHARED BOARD EXPORT",
              "All content below was published to an open board.", "=" * 58]
    for post in posts:
        blocks.extend([
            f"[{post['created_at']}] {post['kind']} #{post['id']}",
            post["title"], "", post["body"],
        ])
        for reply in grouped.get(post["id"], []):
            blocks.extend(["", f"↳ Reply [{reply['created_at']}]", reply["body"]])
        blocks.extend(["", "-" * 58])
    return "\n".join(blocks) + "\n" if posts else "No posts yet.\n"


# ─── DESIGN ────────────────────────────────────────────────────────────────────
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Libre+Baskerville:wght@400;700&display=swap');
:root { --ink:#283D34; --sub:#64776B; --canvas:#F7F8F4; --line:#E1E7DE; }
.stApp { background:var(--canvas); color:var(--ink); }
html,body,[class*="css"],.stApp {font-family:'DM Sans',-apple-system,BlinkMacSystemFont,sans-serif;}
[data-testid="stHeader"] {background:transparent;}
.block-container {max-width:980px;padding-top:2.3rem;padding-bottom:4.5rem;}
.hero {padding:44px 44px 35px;border:1px solid #D9E3D8;border-radius:22px;
 background:radial-gradient(ellipse at 88% 8%,#DDECE3 0%,transparent 59%),linear-gradient(115deg,#F4F5EF,#FAFBF7);margin-bottom:22px;}
.kicker {font-size:11px;letter-spacing:.2em;text-transform:uppercase;font-weight:700;color:#668373;margin-bottom:14px;}
.brand {font-family:'Libre Baskerville',Georgia,serif;font-weight:700;letter-spacing:-.058em;
 font-size:clamp(36px,5vw,54px);color:#2C493B;line-height:1.15;margin:0 0 20px;}
.lede {font-size:15px;color:#596D60;line-height:1.83;max-width:595px;margin:0;}
.hero-detail {color:#64796B;font-size:11px;letter-spacing:.075em;margin-top:27px;}
.public-strip {background:#EDF1EC;border:1px solid #DBE5DA;border-radius:12px;padding:12px 16px;
 color:#476452;font-size:12px;line-height:1.65;margin:0 0 22px;}
.section-title {font-family:'Libre Baskerville',Georgia,serif;font-size:23px;color:var(--ink);
 letter-spacing:-.035em;margin:19px 0 8px;}
.section-note {font-size:13px;color:#66796D;line-height:1.7;margin:0 0 19px;}
.paper {border:1px solid #E1E5DC;border-top:3px solid var(--tone,#758F7E);background:#FFF;
 border-radius:13px;padding:23px 25px 21px;margin:14px 0 3px;box-shadow:0 4px 20px rgba(45,63,48,.035);}
.paper-meta {color:#718176;font-size:11px;text-transform:uppercase;letter-spacing:.09em;font-weight:600;}
.paper-title {font-family:'Libre Baskerville',Georgia,serif;font-size:20px;color:#293D32;
 line-height:1.46;margin:12px 0 11px;overflow-wrap:anywhere;}
.paper-body {white-space:pre-wrap;overflow-wrap:anywhere;color:#34483B;line-height:1.85;font-size:14px;}
.paper-trail {margin-top:13px;color:#79897D;font-size:11px;}
.reply-paper {border-left:2px solid #B6C9B9;background:#F3F6F1;border-radius:0 10px 10px 0;
 padding:12px 17px;margin:10px 0 10px 15px;}
.reply-meta {font-size:11px;color:#69816F;font-weight:600;margin-bottom:5px;}
.footer-note {color:#819086;font-size:11px;line-height:1.8;margin-top:34px;text-align:center;}
.stButton>button[kind="primary"], .stFormSubmitButton>button[kind="primary"] {background:#315447!important;
 border-color:#315447!important;color:white!important;border-radius:10px!important;font-weight:600!important;}
.stButton>button,.stFormSubmitButton>button {border-radius:9px!important;}
[data-baseweb="tab"] {font-size:13px!important;}
[data-testid="stMetricValue"] {font-family:'Libre Baskerville',Georgia,serif;color:#2C493B;}
@media(max-width:670px){.block-container{padding:1.2rem .85rem 3rem;}.hero{padding:29px 22px 26px;}
 .paper{padding:18px 16px;}.hero-detail{letter-spacing:.035em;}}
</style>
"""


def local_time(iso_ts: str) -> str:
    try:
        try:
            tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "America/Toronto"))
        except (KeyError, ValueError):
            tz = timezone.utc
        return datetime.fromisoformat(iso_ts).astimezone(tz).strftime("%b %d, %Y · %I:%M %p")
    except (ValueError, TypeError):
        return "Recently"


def safe(text: str) -> str:
    return html.escape(text, quote=True)


def header() -> None:
    st.markdown("""
       <section class="hero">
         <div class="kicker">A small place for meaningful words</div>
         <div class="brand">Between Us<span style="color:#93B09D">.</span></div>
         <p class="lede">Little updates. Long letters. Things worth putting into words.
           A shared space to leave a note, pick up a conversation, and come back to it.</p>
         <div class="hero-detail">NO LOGIN &nbsp;·&nbsp; NO NAMES &nbsp;·&nbsp; OPEN SHARED BOARD</div>
       </section>
       <div class="public-strip"><strong>Open by design.</strong> Anyone with access to this link
         can read the board, post a note, and reply. Please don't share secrets or personal
         information here. There are no private messages.</div>
    """, unsafe_allow_html=True)


def full_body(body: str) -> None:
    st.markdown(f'<div class="paper-body">{safe(body)}</div>', unsafe_allow_html=True)


def note_card(post: sqlite3.Row, scope: str) -> None:
    body = post["body"]
    abbreviated = len(body) > 850
    snippet = (body[:850].rstrip() + "…") if abbreviated else body
    tone = post["accent"] if post["accent"] in COLORS.values() else COLORS["Sage"]
    count = post["reply_count"]
    st.markdown(f"""
      <article class="paper" style="--tone:{tone}">
        <div class="paper-meta">{safe(post['kind'])} · {safe(local_time(post['created_at']))}</div>
        <div class="paper-title">{safe(post['title'])}</div>
        <div class="paper-body">{safe(snippet)}</div>
        <div class="paper-trail">{len(body.split()):,} words · {count} {'reply' if count==1 else 'replies'}</div>
      </article>
    """, unsafe_allow_html=True)
    if abbreviated:
        with st.expander("Read the entire letter / note"):
            full_body(body)
    with st.expander(f"Conversation ({count})" if count else "Leave a reply", expanded=False):
        replies = list_replies(post["id"])
        if replies:
            for row in replies:
                st.markdown(f"""
                    <div class="reply-paper">
                      <div class="reply-meta">Reply · {safe(local_time(row['created_at']))}</div>
                      <div class="paper-body">{safe(row['body'])}</div>
                    </div>
                """, unsafe_allow_html=True)
        else:
            st.caption("No replies yet. You can start the conversation.")
        with st.form(f"reply_form_{scope}_{post['id']}", clear_on_submit=True):
            reply = st.text_area("Your reply", key=f"reply_text_{scope}_{post['id']}",
                                 max_chars=MAX_MESSAGE_CHARS, height=110,
                                 placeholder="Write a response…")
            if st.form_submit_button("Post reply", type="primary"):
                try:
                    add_reply(post["id"], reply)
                except (ValueError, sqlite3.Error) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()


@st.fragment(run_every=REFRESH_INTERVAL)
def live_board(search: str, kind: str, limit: int, scope: str) -> None:
    posts = list_posts(search, kind, limit)
    if not posts:
        st.info("No notes here yet. You can be the first to write one.")
        return
    st.caption(f"Showing {len(posts)} most recent matching posts · Updates every 15 seconds")
    for post in posts:
        note_card(post, scope)
    if scope == "main" and len(posts) >= limit and limit < 250:
        if st.button("Show more notes", key=f"more_notes_{scope}", use_container_width=True):
            st.session_state["feed_limit"] = min(250, limit + PAGE_SIZE)
            st.rerun()


def write_page() -> None:
    st.markdown('<div class="section-title">Leave something here</div>', unsafe_allow_html=True)
    st.markdown('<p class="section-note">A few words, or many pages. No account, no signature, no pressure to make it perfect.</p>', unsafe_allow_html=True)
    with st.form("publish_post", clear_on_submit=True):
        a, b = st.columns(2)
        with a:
            kind = st.radio("Write a", ("Note", "Letter"), horizontal=True)
        with b:
            tone = st.selectbox("Paper accent", list(COLORS.keys()))
        title = st.text_input("Title (optional)", max_chars=MAX_TITLE_CHARS,
                              placeholder="Something I've been thinking about…")
        body = st.text_area("Your message", height=315, max_chars=MAX_MESSAGE_CHARS,
                            placeholder="Start anywhere. Write as much or as little as you need.")
        st.caption("One sentence is enough. Longer writing is welcome (up to 100,000 characters).")
        st.warning("Before publishing: **everything posted here is visible to anyone who opens this board**. Posts cannot be edited or removed through the public interface.", icon="🔓")
        if st.form_submit_button("Publish to the board →", type="primary", use_container_width=True):
            try:
                add_post(kind, title, body, COLORS[tone])
            except (ValueError, sqlite3.Error) as exc:
                st.error(str(exc))
            else:
                st.success("Your message is now on the board. Open Conversations to see it.")


def find_and_export_page() -> None:
    st.markdown('<div class="section-title">Find your way back</div>', unsafe_allow_html=True)
    st.markdown('<p class="section-note">Search notes, letters, and replies. Everything here is shared, not private.</p>', unsafe_allow_html=True)
    term = st.text_input("Search the board", placeholder="A word, thought, or title…")
    kind = st.selectbox("Show", ["All", "Note", "Letter"], index=0)
    if term.strip() or kind != "All":
        live_board(term, kind, 100, "search")
    else:
        st.caption("Enter a word or choose a format to find a conversation.")
    st.divider()
    st.markdown("**Keep a copy**")
    st.caption("Export includes all messages and replies posted to this public board.")
    st.download_button("↓ Download entire board (.txt)", data=export_all(),
                       file_name="between-us-open-board.txt", mime="text/plain")


@st.fragment(run_every=REFRESH_INTERVAL)
def live_stats() -> None:
    posts, letters, replies = board_counts()
    c1, c2, c3 = st.columns(3)
    c1.metric("Posts", posts)
    c2.metric("Letters", letters)
    c3.metric("Replies", replies)


def main() -> None:
    init_db()
    st.markdown(CSS, unsafe_allow_html=True)
    header()
    live_stats()
    tab_board, tab_write, tab_find = st.tabs(
        ["✉ Conversations", "✍ Write", "⌕ Search & export"]
    )
    with tab_board:
        st.markdown('<div class="section-title">Conversations</div>', unsafe_allow_html=True)
        st.markdown('<p class="section-note">Words left here for others to read. The newest notes appear first.</p>', unsafe_allow_html=True)
        limit = st.session_state.get("feed_limit", PAGE_SIZE)
        live_board("", "All", limit, "main")
    with tab_write:
        write_page()
    with tab_find:
        find_and_export_page()
    st.markdown("""
      <div class="footer-note">Between Us · Open shared board · No identities or private inboxes<br>
      Content is stored on the app server, not end-to-end encrypted. Server restarts may clear local storage.</div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
