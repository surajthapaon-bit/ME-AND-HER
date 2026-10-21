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
    page_title="Between Us — a quiet writing space",
    page_icon="✉️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BOARD_DB_PATH = os.environ.get("BETWEEN_US_DB_PATH", "between_us_open.db")
MAX_MESSAGE_CHARS = 100_000
MAX_TITLE_CHARS = 140
PAGE_SIZE = 20
REFRESH_INTERVAL = "20s"
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


# ─── VISUAL SYSTEM ────────────────────────────────────────────────────────────
# One open canvas, quiet typography, and a distinct paper-like writing surface.
# The explicit widget colors also fix low-contrast labels on dark-mode browsers.
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;450;500;550;600;650;700&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&display=swap');
:root {
  --bu-canvas: #F7F7F3;
  --bu-paper: #FFFDF9;
  --bu-ink: #25372F;
  --bu-sub: #607168;
  --bu-light: #7A8880;
  --bu-line: #DDE3DC;
  --bu-accent: #355A4A;
  --bu-sage: #E9EFE8;
}
html, body, .stApp {
  font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
  background: var(--bu-canvas) !important;
  color: var(--bu-ink) !important;
}
[data-testid="stHeader"] {background: transparent !important;}
[data-testid="stDecoration"] {display:none !important;}
[data-testid="stToolbar"] {color:var(--bu-ink);}
.block-container {max-width:860px !important; padding-top:3.3rem !important; padding-bottom:4.5rem !important;}
.stApp [data-testid="stMarkdownContainer"] {color:var(--bu-ink);}
.stApp [data-testid="stCaptionContainer"],
.stApp [data-testid="stCaptionContainer"] p {color:var(--bu-sub) !important;}

/* Masthead: no large gradient panels or needless dashboard chrome. */
.bu-topline {display:flex;justify-content:space-between;align-items:center;gap:12px;
  padding:0 0 30px;}
.bu-overline {font-size:10px;line-height:1.6;font-weight:700;letter-spacing:.19em;
  color:#5D796A;text-transform:uppercase;}
.bu-live {font-size:11px;color:#687C70;display:inline-flex;gap:8px;align-items:center;white-space:nowrap;}
.bu-live:before {content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:#739685;}
.bu-wordmark {margin:0;color:#263C30;font-family:'Newsreader',Georgia,serif;
  font-weight:500;font-size:clamp(49px,8vw,74px);letter-spacing:-.065em;line-height:1.04;}
.bu-wordmark span {font-style:italic;font-weight:400;}
.bu-wordmark .bu-dot {color:#89A393;font-style:normal;}
.bu-deck {font-size:15px;line-height:1.9;color:#617169;max-width:490px;margin:13px 0 0;}
.bu-rule {border-top:1px solid var(--bu-line);margin:36px 0 16px;}
.bu-disclosure {display:flex;align-items:flex-start;gap:10px;padding:12px 0 15px;
  color:#607168;font-size:12px;line-height:1.65;}
.bu-disclosure strong {color:#334F41;font-weight:650;white-space:nowrap;}
.bu-ministats {display:flex;align-items:center;flex-wrap:wrap;gap:8px 21px;
  margin:7px 0 11px;color:#718178;font-size:11px;letter-spacing:.035em;}
.bu-ministats b {color:#3D5D4B;font-weight:700;font-variant-numeric:tabular-nums;}
.bu-ministats .bu-div {color:#BCC8BE;}

/* Navigation: high-contrast in browsers with a dark Streamlit theme. */
.stApp [data-baseweb="tab-list"] {gap:8px !important;border-bottom:1px solid var(--bu-line) !important;}
.stApp [data-baseweb="tab"] {
  font-family:'DM Sans',sans-serif !important;font-size:13px !important;
  font-weight:650 !important;color:#6C7D72 !important;
  padding:13px 18px 15px !important;background:transparent !important;
}
.stApp [data-baseweb="tab"][aria-selected="true"] {color:#264F3C !important;}
.stApp [data-baseweb="tab-highlight"] {background:#315B47 !important;height:2px !important;}
.stApp [data-baseweb="tab-border"] {background:transparent !important;}
.stApp [data-testid="stTabs"] [data-baseweb="tab-panel"] {padding-top:11px !important;}

/* Section hierarchy. */
.bu-eyebrow {font-size:10px;text-transform:uppercase;letter-spacing:.17em;font-weight:700;
  color:#789081;margin:23px 0 9px;}
.bu-section-title {font-family:'Newsreader',Georgia,serif;color:#263C30;
  font-size:clamp(31px,5vw,43px);line-height:1.2;letter-spacing:-.045em;font-weight:500;margin:0 0 7px;}
.bu-section-lead {font-size:13px;line-height:1.8;color:#6E7D74;max-width:595px;margin:0 0 27px;}
.bu-empty {border:1px dashed #C9D5C9;border-radius:16px;padding:38px 27px;
  background:#F9FAF6;color:#6E7C72;font-size:14px;line-height:1.8;text-align:center;}
.bu-empty strong {font-family:'Newsreader',Georgia,serif;font-size:27px;font-weight:500;color:#354D3D;}
.bu-timestamp {font-variant-numeric:tabular-nums;}

/* Posts: paper rather than stacked app cards. */
.bu-post {position:relative;background:var(--bu-paper);border:1px solid #E0E4DC;
  border-radius:13px;padding:25px 29px 23px;margin:18px 0 8px;
  box-shadow:0 5px 23px rgba(39,58,45,.025);}
.bu-post:before {content:'';height:3px;width:36px;position:absolute;left:29px;top:-1px;
  background:var(--post-accent,#758F7E);border-radius:0 0 4px 4px;}
.bu-post-meta {display:flex;gap:12px;justify-content:space-between;align-items:center;flex-wrap:wrap;
  color:#77877D;font-size:10px;letter-spacing:.075em;text-transform:uppercase;font-weight:650;line-height:1.7;}
.bu-post-title {font-family:'Newsreader',Georgia,serif;font-weight:500;font-size:26px;
  letter-spacing:-.035em;color:#2C4134;line-height:1.32;margin:19px 0 12px;overflow-wrap:anywhere;}
.bu-post-body {font-family:'DM Sans',sans-serif;white-space:pre-wrap;overflow-wrap:anywhere;
  font-size:14px;line-height:1.95;color:#46564B;}
.bu-post-foot {margin-top:22px;padding-top:12px;border-top:1px solid #EEF0EA;
  font-size:11px;color:#839087;display:flex;gap:9px;}
.bu-reply {border-left:2px solid #9EB6A5;padding:8px 0 8px 17px;margin:15px 0;
  background:transparent;}
.bu-reply-meta {font-size:10px;text-transform:uppercase;letter-spacing:.08em;font-weight:700;
  color:#819286;margin-bottom:7px;}
.bu-footer {margin-top:54px;border-top:1px solid var(--bu-line);padding-top:19px;
  font-size:11px;line-height:1.8;color:#87938B;text-align:center;}

/* Streamlit forms and BaseWeb controls: a warm, legible writing environment. */
.stApp [data-testid="stForm"] {background:var(--bu-paper) !important;
  border:1px solid #E0E5DC !important;border-radius:17px !important;
  padding:32px 36px 34px !important;box-shadow:0 8px 30px rgba(43,61,47,.027);}
.stApp [data-testid="stForm"]:not(:has(.bu-paper-heading)) {background:transparent !important;
  border:none !important;box-shadow:none !important;padding:8px 0 0 !important;}
.bu-paper-heading {font-family:'Newsreader',Georgia,serif;font-size:27px;line-height:1.15;
  font-weight:500;letter-spacing:-.035em;color:#2D4335;margin-bottom:10px;}
.bu-paper-sub {color:#7C887D;font-size:12px;line-height:1.7;margin-bottom:21px;}
.stApp [data-testid="stForm"] label,
.stApp [data-testid="stForm"] label p,
.stApp [data-testid="stForm"] [data-testid="stWidgetLabel"],
.stApp [data-testid="stForm"] [data-testid="stWidgetLabel"] p,
.stApp [data-testid="stTextInput"] label p,
.stApp [data-testid="stTextArea"] label p,
.stApp [data-testid="stSelectbox"] label p,
.stApp [data-testid="stRadio"] label p {color:#42554A !important;font-size:12px !important;font-weight:650 !important;}
.stApp [data-baseweb="input"], .stApp [data-baseweb="textarea"],
.stApp [data-baseweb="select"] > div,
.stApp [data-baseweb="select"] > div > div {
  background:#F9FAF6 !important;color:#263D30 !important;border-color:#D9E2D7 !important;
}
.stApp [data-baseweb="input"], .stApp [data-baseweb="textarea"],
.stApp [data-baseweb="select"] > div {
  border-radius:10px !important;box-shadow:none !important;
}
.stApp input, .stApp textarea, .stApp [data-baseweb="select"] span,
.stApp [data-baseweb="select"] [aria-selected] {color:#24392D !important;
  -webkit-text-fill-color:#24392D !important;font-family:'DM Sans',sans-serif !important;font-size:15px !important;}
.stApp input::placeholder,.stApp textarea::placeholder {
  color:#9AA79E !important;-webkit-text-fill-color:#9AA79E !important;opacity:1 !important;
}
.stApp textarea {font-size:16px !important;line-height:1.85 !important;
  padding:17px 17px !important;caret-color:#315C47 !important;}
.stApp input {caret-color:#315C47 !important;}
.stApp [data-baseweb="input"]:focus-within,
.stApp [data-baseweb="textarea"]:focus-within,
.stApp [data-baseweb="select"]:focus-within {border-color:#7F9C87 !important;
  box-shadow:0 0 0 2px rgba(81,113,88,.1) !important;}
.stApp [data-testid="stRadio"] div[role="radiogroup"] {gap:14px;}
.stApp [data-testid="stRadio"] label,
.stApp [data-testid="stRadio"] label span,
.stApp [data-testid="stRadio"] label p {color:#334C3B !important;}
.stApp [data-testid="stRadio"] input {accent-color:#356249 !important;}
.stApp [data-baseweb="radio"] div[aria-checked="true"] {background:#356249 !important;}
.stApp [data-baseweb="radio"] [data-testid="stMarkdownContainer"] {color:#334C3B !important;}
.stApp [data-testid="stForm"] [data-testid="stCaptionContainer"] p {color:#7B8B7E !important;}
.stApp [data-testid="stFormSubmitButton"] button[kind="primary"],
.stApp [data-testid="stButton"] button[kind="primary"],
.stApp button[kind="primary"] {background:#355F49 !important;
  border:1px solid #355F49 !important;color:#FFFFFF !important;
  border-radius:10px !important;min-height:47px !important;
  font-family:'DM Sans',sans-serif !important;font-weight:650 !important;
  box-shadow:none !important;}
.stApp button[kind="primary"]:hover {background:#284B38 !important;border-color:#284B38 !important;}
.stApp button[kind="secondary"] {color:#365541 !important;
  border:1px solid #C8D4C8 !important;background:#FAFBF8 !important;
  border-radius:9px !important;}
.stApp button {font-family:'DM Sans',sans-serif !important;}
.stApp [data-testid="stExpander"] {border:1px solid #E0E7DE !important;
  border-radius:10px !important;background:transparent !important;}
.stApp [data-testid="stExpander"] summary,
.stApp [data-testid="stExpander"] summary p {color:#486350 !important;
  font-size:12px !important;font-weight:600 !important;}
.stApp [data-testid="stDownloadButton"] button {color:#365541 !important;background:#F9FAF7 !important;border-color:#CEDBD0 !important;}
.bu-publish-note {font-size:11px;line-height:1.65;color:#819087;margin:12px 0 17px;}
.bu-publish-note strong {color:#576D5C;font-weight:650;}

@media (max-width:700px) {
  .block-container {padding:3.1rem 1.15rem 3rem !important;}
  .bu-topline {padding-bottom:24px;}
  .bu-wordmark {font-size:clamp(52px,12.2vw,68px);}
  .bu-deck {font-size:14px;line-height:1.75;}
  .bu-rule {margin-top:28px;}
  .bu-disclosure {font-size:11px;gap:8px;}
  .bu-ministats {gap:7px 11px;}
  .stApp [data-baseweb="tab"] {padding:13px 15px 14px !important;}
  .bu-section-title {font-size:35px;}
  .bu-post {padding:22px 20px 20px;}
  .bu-post:before {left:20px;}
  .bu-post-title {font-size:25px;}
  .stApp [data-testid="stForm"] {padding:25px 20px 27px !important;}
  .stApp textarea {font-size:16px !important;}
}
@media (prefers-reduced-motion: reduce) {
  * {scroll-behavior:auto !important; transition-duration:0.01ms !important;}
}
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


def masthead() -> None:
    st.markdown("""
      <header>
        <div class="bu-topline">
          <span class="bu-overline">A space to leave words</span>
          <span class="bu-live">Open board</span>
        </div>
        <h1 class="bu-wordmark">Between <span>Us<span class="bu-dot">.</span></span></h1>
        <p class="bu-deck">Small notes, long letters, and the conversations in between.</p>
        <div class="bu-rule"></div>
        <div class="bu-disclosure"><strong>Open to everyone.</strong>
          <span>Anyone with this link can read and contribute. There are no private messages.</span>
        </div>
      </header>
    """, unsafe_allow_html=True)


def section(eyebrow: str, title: str, lead: str) -> None:
    st.markdown(f"""
      <div class="bu-eyebrow">{safe(eyebrow)}</div>
      <h2 class="bu-section-title">{safe(title)}</h2>
      <p class="bu-section-lead">{safe(lead)}</p>
    """, unsafe_allow_html=True)


def full_body(body: str) -> None:
    st.markdown(f'<div class="bu-post-body">{safe(body)}</div>', unsafe_allow_html=True)


def post_card(post: sqlite3.Row, scope: str) -> None:
    body = post["body"]
    abbreviated = len(body) > 1000
    snippet = (body[:1000].rstrip() + "…") if abbreviated else body
    accent = post["accent"] if post["accent"] in COLORS.values() else COLORS["Sage"]
    count = post["reply_count"]
    st.markdown(f"""
      <article class="bu-post" style="--post-accent:{accent}">
        <div class="bu-post-meta">
          <span>{safe(post['kind'])}</span>
          <span class="bu-timestamp">{safe(local_time(post['created_at']))}</span>
        </div>
        <h3 class="bu-post-title">{safe(post['title'])}</h3>
        <div class="bu-post-body">{safe(snippet)}</div>
        <div class="bu-post-foot">{len(body.split()):,} words <span>·</span>
          {count} {'reply' if count == 1 else 'replies'}</div>
      </article>
    """, unsafe_allow_html=True)
    if abbreviated:
        with st.expander("Read the full message"):
            full_body(body)
    with st.expander(f"Conversation · {count} {'reply' if count == 1 else 'replies'}" if count else "Write a reply"):
        replies = list_replies(post["id"])
        for reply in replies:
            st.markdown(f"""
              <div class="bu-reply">
                <div class="bu-reply-meta">Reply · {safe(local_time(reply['created_at']))}</div>
                <div class="bu-post-body">{safe(reply['body'])}</div>
              </div>
            """, unsafe_allow_html=True)
        if not replies:
            st.caption("No replies yet. Leave the first one.")
        with st.form(f"reply_{scope}_{post['id']}", clear_on_submit=True, border=False):
            reply = st.text_area("Your reply", height=130, max_chars=MAX_MESSAGE_CHARS,
                                 placeholder="Add a thought to this conversation…")
            if st.form_submit_button("Post reply", type="primary", use_container_width=False):
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
        st.markdown("""
          <div class="bu-empty"><strong>Still a quiet page.</strong><div>
          Nothing has been shared here yet. The first note is yours to write.</div></div>
        """, unsafe_allow_html=True)
        return
    for post in posts:
        post_card(post, scope)
    if scope == "main" and len(posts) >= limit and limit < 250:
        if st.button("See more conversations", key=f"more_posts_{scope}", use_container_width=True):
            st.session_state["feed_limit"] = min(250, limit + PAGE_SIZE)
            st.rerun()


@st.fragment(run_every=REFRESH_INTERVAL)
def quiet_stats() -> None:
    posts, letters, replies = board_counts()
    st.markdown(f"""
      <div class="bu-ministats">
         <span><b>{posts}</b> {'post' if posts == 1 else 'posts'}</span>
         <span class="bu-div">·</span>
         <span><b>{letters}</b> {'letter' if letters == 1 else 'letters'}</span>
         <span class="bu-div">·</span>
         <span><b>{replies}</b> {'reply' if replies == 1 else 'replies'}</span>
      </div>
    """, unsafe_allow_html=True)


def write_page() -> None:
    section("The writing desk", "Leave something here.",
            "A quick thought or a letter that takes its time. The page is yours.")
    with st.form("compose", clear_on_submit=True):
        st.markdown("""
          <div class="bu-paper-heading">A fresh page</div>
          <div class="bu-paper-sub">Take your time. It doesn't need to be perfect.</div>
        """, unsafe_allow_html=True)
        col1, col2 = st.columns([1, 1], gap="medium")
        with col1:
            kind = st.radio("Format", ("Note", "Letter"), horizontal=True)
        with col2:
            tone = st.selectbox("Paper accent", list(COLORS), index=0)
        title = st.text_input("Title (optional)", max_chars=MAX_TITLE_CHARS,
                              placeholder="Give these words a title…")
        message = st.text_area("Your words", height=390, max_chars=MAX_MESSAGE_CHARS,
                               placeholder="Start wherever you are.\n\nWrite what you want to leave here…")
        st.markdown("""
          <div class="bu-publish-note"><strong>Before you share:</strong> everything posted here
          is readable by anyone with this link. Messages cannot be edited or deleted in the app.</div>
        """, unsafe_allow_html=True)
        if st.form_submit_button("Publish to the board  →", type="primary", use_container_width=True):
            try:
                add_post(kind, title, message, COLORS[tone])
            except (ValueError, sqlite3.Error) as exc:
                st.error(str(exc))
            else:
                st.success("Published. Your words are now on the board — open Read to see them.")


def find_page() -> None:
    section("The archive", "Find a conversation.",
            "Search for a phrase or return to an earlier note.")
    with st.container():
        col1, col2 = st.columns([2, 1], gap="medium")
        with col1:
            query = st.text_input("Search the board", placeholder="A word, title, or thought…")
        with col2:
            kind = st.selectbox("Show", ["All", "Note", "Letter"])
        if query.strip() or kind != "All":
            live_board(query, kind, 100, "search")
        else:
            st.caption("Search includes replies as well as the original notes.")
        st.divider()
        st.markdown("**Keep a copy**")
        st.caption("This download contains every public post and reply, not just your own writing.")
        st.download_button("Download the board (.txt)", data=export_all(),
                           file_name="between-us-board.txt", mime="text/plain")


def main() -> None:
    init_db()
    st.markdown(CSS, unsafe_allow_html=True)
    masthead()
    quiet_stats()
    tab_read, tab_write, tab_find = st.tabs(["Read", "Write", "Find"])
    with tab_read:
        section("Conversations", "Words worth keeping.",
                "Notes and letters left on this shared page. Newest first.")
        live_board("", "All", st.session_state.get("feed_limit", PAGE_SIZE), "main")
    with tab_write:
        write_page()
    with tab_find:
        find_page()
    st.markdown("""
      <footer class="bu-footer">Between Us · An open space for writing<br>
      Not end-to-end encrypted. Streamlit's local database may be cleared on redeployment.</footer>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
