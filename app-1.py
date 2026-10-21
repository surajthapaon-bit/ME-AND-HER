"""Between Us — a name-free board for notes, letters, and conversations.

Run: streamlit run app.py
No room passphrase or Streamlit Secrets configuration needed.
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st
from storage import Board, MAX_BODY_LENGTH, PALETTE

st.set_page_config(
    page_title="Between Us · Notes & Letters",
    page_icon="✉️",
    layout="wide",
    initial_sidebar_state="expanded",
)

REFRESH_SECONDS = 7
PREVIEW_CHARS = 560


STYLES = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Libre+Baskerville:wght@400;700&display=swap');
:root {--ink:#252924;--muted:#6c756f;--line:#e4e6e0;--cream:#f8f9f6;--accent:#314e46;}
.stApp { background:var(--cream); color:var(--ink); font-family:'DM Sans',sans-serif; }
[data-testid="stHeader"] {background:transparent;}
[data-testid="stSidebar"] {background:#eef1eb;border-right:1px solid #dfe5dd;}
.block-container {max-width:1080px;padding-top:2.2rem;padding-bottom:5rem;}
h1,h2,h3 {letter-spacing:-.025em;}
.hero {border-radius:22px;padding:34px 38px 26px;border:1px solid #dce3db;
background:radial-gradient(circle at top right,#e2eee8,transparent 52%),linear-gradient(120deg,#f4f5ef,#f7f8f5);
margin-bottom:24px;}
.eyebrow {text-transform:uppercase;letter-spacing:.17em;color:#567467;font-size:11px;font-weight:700;margin-bottom:11px;}
.brand {font-family:'Libre Baskerville',Georgia,serif;font-size:clamp(34px,4.8vw,54px);
font-weight:700;line-height:1.18;color:#263d35;letter-spacing:-.06em;margin:0 0 14px;}
.hero-desc {max-width:660px;color:#59685f;font-size:15px;line-height:1.7;margin:0;}
.hero-foot {color:#60756a;font-size:11px;letter-spacing:.04em;margin-top:20px;}
.note {border:1px solid #e0e6df;border-left:4px solid var(--note-color);border-radius:14px;
background:#fff;padding:22px 24px;margin:12px 0 6px;box-shadow:0 3px 18px rgba(35,45,37,.035);}
.note--reply {margin:7px 0 4px 20px;padding:13px 17px;border-radius:10px;box-shadow:none;}
.note-meta {display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap;
font-size:11px;line-height:1.4;letter-spacing:.025em;color:#63736a;margin-bottom:10px;}
.note-title {font-family:'Libre Baskerville',Georgia,serif;font-size:20px;line-height:1.46;
color:#273b32;margin:3px 0 12px;overflow-wrap:anywhere;}
.note-body {font-size:14px;line-height:1.85;color:#34443b;white-space:pre-wrap;overflow-wrap:anywhere;}
.note--reply .note-body {font-size:13px;line-height:1.75;}
.note-pill {background:#eaf1eb;border-radius:20px;padding:4px 9px;font-size:10px;font-weight:700;color:#43604e;}
.note-pill.new {background:#fce9dc;color:#885e37;}
.note-footer {font-size:11px;color:#78867c;display:flex;gap:12px;margin-top:13px;}
.section-heading {font-family:'Libre Baskerville',Georgia,serif;font-size:24px;color:#273b32;margin:14px 0 8px;}
.subtitle {font-size:13px;color:#718076;line-height:1.7;margin-bottom:20px;}
.softbox {border:1px solid #dae2d8;background:#f3f7f1;border-radius:12px;padding:15px 18px;
color:#52695a;font-size:13px;line-height:1.6;margin:8px 0 20px;}
.stButton>button[kind="primary"], .stFormSubmitButton>button[kind="primary"] {
 background:#315447 !important;border-color:#315447 !important;color:#fff !important;
 border-radius:9px !important;font-weight:650 !important;}
.stButton>button, .stFormSubmitButton>button {border-radius:9px !important;}
[data-baseweb="tab-list"] {gap:7px;}
[data-baseweb="tab"] {font-size:13px;padding-left:13px;padding-right:13px;}
[data-testid="stMetricValue"] {color:#2b483b;font-family:'Libre Baskerville',Georgia,serif;}
@media(max-width:680px){.block-container{padding:1.1rem .85rem 3rem}.hero{padding:26px 23px}.note{padding:17px 17px}.note--reply{margin-left:6px}}
</style>
"""


def config_value(key: str, default: str | None = None) -> str | None:
    val = os.environ.get(key)
    if val:
        return val
    try:
        return st.secrets.get(key, default)
    except Exception:
        # Streamlit raises an exception if no secrets file has been configured.
        return default


def room_timezone():
    try:
        return ZoneInfo(config_value("APP_TIMEZONE", "America/Toronto") or "UTC")
    except (KeyError, ValueError):
        return timezone.utc


def format_time(iso_timestamp: str) -> str:
    try:
        local = datetime.fromisoformat(iso_timestamp).astimezone(room_timezone())
        return local.strftime("%b %d · %I:%M %p %Z").replace(" 0", " ")
    except (ValueError, TypeError):
        return "Recently"


def member_label(member_id: int, me: int) -> str:
    return "You" if member_id == me else f"Member {member_id:02d}"


def heading() -> None:
    st.markdown("""
        <section class="hero">
          <div class="eyebrow">A quiet space for people who matter</div>
          <div class="brand">Between Us<span style="color:#9cb4a5">.</span></div>
          <p class="hero-desc">Notes for everyday moments. Letters for the things that take longer
          to say. One calm place to share, return, and stay connected.</p>
          <div class="hero-foot">NO SHARED PASSCODE &nbsp;·&nbsp; NO REAL NAMES REQUIRED &nbsp;·&nbsp; LETTERS &amp; CONVERSATIONS</div>
        </section>
    """, unsafe_allow_html=True)


def post_card(row, member_id: int, *, is_reply: bool = False, is_unread: bool = False,
              count: int = 0, read_status: str = "") -> None:
    removed = row["deleted_at"] is not None
    dest = "The room" if row["recipient_id"] is None else member_label(row["recipient_id"], member_id)
    author = member_label(row["author_id"], member_id)
    kind = "Reply" if is_reply else row["kind"]
    meta = f"{html.escape(kind)} · {html.escape(author)} → {html.escape(dest)}"
    status_pill = '<span class="note-pill new">NEW</span>' if is_unread else ""
    title = "Message removed" if removed else row["title"]
    body = "This message was removed by its author." if removed else row["body"]
    long = len(body) > PREVIEW_CHARS
    text = (body[:PREVIEW_CHARS].rstrip() + "…") if long else body
    title_markup = f'<div class="note-title">{html.escape(title)}</div>' if title and not is_reply else ""
    tail = []
    if not is_reply and count:
        tail.append(f"{count} {'reply' if count == 1 else 'replies'}")
    if read_status:
        tail.append(read_status)
    if not removed and len(body) > PREVIEW_CHARS:
        tail.append(f"{len(body.split()):,} words")
    footer = f'<div class="note-footer">{" · ".join(html.escape(x) for x in tail)}</div>' if tail else ""
    st.markdown(f"""
        <div class="note {'note--reply' if is_reply else ''}" style="--note-color:{html.escape(row['accent'])}">
          <div class="note-meta"><span>{meta}</span>
            <span>{html.escape(format_time(row['created_at']))} &nbsp;{status_pill}</span></div>
          {title_markup}
          <div class="note-body">{html.escape(text)}</div>
          {footer}
        </div>
    """, unsafe_allow_html=True)
    if long:
        with st.expander("Read the complete message", expanded=False):
            st.markdown(f'<div class="note-body">{html.escape(body)}</div>', unsafe_allow_html=True)


def clear_login() -> None:
    # Clear drafts and all widget state too: another person may use this device.
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def entry(board: Board) -> int:
    if "member_id" not in st.session_state:
        heading()
        st.markdown('<div class="section-heading">Your private place</div>', unsafe_allow_html=True)
        st.caption("No usernames or emails. Your individual return key protects your direct messages.")
        returning, new = st.tabs(["I have a return key", "I'm new here"])
        with returning:
            with st.form("returning", clear_on_submit=True):
                key = st.text_input("Your personal return key", type="password", autocomplete="off")
                if st.form_submit_button("Continue", type="primary"):
                    member_id = board.login(key)
                    if member_id is None:
                        st.error("That key wasn't found. Check it and try again.")
                    else:
                        st.session_state["member_id"] = member_id
                        st.rerun()
        with new:
            st.write("Create an anonymous seat to write, reply, and track what you've read.")
            st.info("Anyone with this app link can join and read messages addressed to Everyone.")
            st.warning("You'll receive a one-time return key. Save it somewhere safe; it cannot be recovered.", icon="🔐")
            if st.button("Create my private seat", type="primary"):
                member_id, key = board.create_participant()
                st.session_state["member_id"] = member_id
                st.session_state["new_private_key"] = key
                st.rerun()
        st.stop()

    return int(st.session_state["member_id"])


def show_new_key() -> None:
    key = st.session_state.get("new_private_key")
    if not key:
        return
    st.warning("**Save your personal return key now.** You'll need it after refreshing or closing the page. Never share it with other people.", icon="🔐")
    st.code(key, language=None)
    if st.button("I've saved my key", type="primary"):
        st.session_state.pop("new_private_key", None)
        st.rerun()
    st.divider()


def choose_recipient(others: list[int]) -> int | None:
    labels = ["Everyone in the room"] + [f"Member {i:02d}" for i in others]
    answer = st.selectbox("Share with", labels, key="compose_recipient")
    return None if answer == labels[0] else others[labels.index(answer) - 1]


def compose(board: Board, member_id: int) -> None:
    st.markdown('<div class="section-heading">Write something that matters</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">A small note, a long letter, or anything in between. Only intended recipients can read private messages.</div>', unsafe_allow_html=True)
    others = [m for m in board.members() if m != member_id]
    with st.form("new_post", clear_on_submit=True):
        left, right = st.columns(2)
        with left:
            target = choose_recipient(others)
            kind = st.radio("Format", ["Note", "Letter"], horizontal=True)
        with right:
            color_name = st.selectbox("Paper tone", list(PALETTE))
            st.caption("A soft accent keeps the writing at the center.")
        title = st.text_input("Title (optional)", max_chars=140, placeholder="Something I've been meaning to say…")
        body = st.text_area("Your words", height=280, max_chars=MAX_BODY_LENGTH,
                            placeholder="Start anywhere. There is no perfect way to begin.")
        st.caption(f"{len(body.split()):,} words · {len(body):,} characters")
        submitted = st.form_submit_button("Share message →", type="primary")
        if submitted:
            if not body.strip():
                st.error("Write a message before sharing.")
            else:
                board.post(member_id, target, kind, title or "Untitled", body, PALETTE[color_name])
                st.success("Your message was shared.")
                st.rerun()
    st.caption("Anyone with the link can join and read group posts, including earlier ones. Direct notes are visible only to their participants.")


def reply_editor(board: Board, member_id: int) -> None:
    root_id = st.session_state.get("reply_to")
    if not root_id:
        return
    root = board.find_root(root_id, member_id)
    if root is None or root["deleted_at"]:
        st.session_state.pop("reply_to", None)
        return
    dest = "everyone in the room" if root["recipient_id"] is None else (
        f"Member {root['recipient_id']:02d}" if root["author_id"] == member_id
        else f"Member {root['author_id']:02d}"
    )
    with st.container(border=True):
        st.markdown("### Reply to the conversation")
        st.caption(f"Your reply goes to {dest}. Replies to a room post are visible to the whole room.")
        with st.form(f"reply_form_{root_id}", clear_on_submit=True):
            body = st.text_area("Write your reply", height=170, max_chars=MAX_BODY_LENGTH,
                                placeholder="Write your response…")
            if st.form_submit_button("Send reply", type="primary"):
                if body.strip():
                    board.post(member_id, None, "Reply", "", body, root["accent"], parent_id=root_id)
                    st.session_state.pop("reply_to", None)
                    st.rerun()
                else:
                    st.warning("Add a reply first.")
        if st.button("Cancel reply", key="cancel_reply"):
            st.session_state.pop("reply_to", None)
            st.rerun()
    st.divider()


def confirm_remove(board: Board, message_id: int, member_id: int, key: str) -> None:
    confirm_key = f"remove_confirm_{key}_{message_id}"
    if st.session_state.get(confirm_key):
        st.caption("Remove the text from this message? Existing replies will remain.")
        a, b = st.columns([1, 4])
        if a.button("Yes, remove", key=f"yes_{confirm_key}"):
            board.remove_own(message_id, member_id)
            st.session_state.pop(confirm_key, None)
            st.rerun()
        if b.button("Keep it", key=f"no_{confirm_key}"):
            st.session_state.pop(confirm_key, None)
            st.rerun()
    elif st.button("Remove", key=f"remove_{key}_{message_id}", help="Remove your message text"):
        st.session_state[confirm_key] = True
        st.rerun()


def show_thread(board: Board, row, member_id: int, prefix: str) -> None:
    mid = row["id"]
    unread = board.is_unread(mid, member_id)
    status = board.read_status(mid, member_id) if row["author_id"] == member_id else ""
    post_card(row, member_id, is_unread=unread, count=row["reply_count"], read_status=status)
    c1, c2, c3, c4 = st.columns([1.15, 1.2, 1.35, 3.3])
    if row["deleted_at"] is None:
        if c1.button("↩ Reply", key=f"reply_{prefix}_{mid}"):
            st.session_state["reply_to"] = mid
            st.rerun()
    if unread and c2.button("✓ Read", key=f"read_{prefix}_{mid}"):
        board.mark_seen(mid, member_id)
        st.rerun()
    saved = bool(row["is_saved"])
    if c3.button("★ Saved" if saved else "☆ Save", key=f"save_{prefix}_{mid}"):
        board.toggle_saved(mid, member_id)
        st.rerun()

    if row["author_id"] == member_id and row["deleted_at"] is None:
        with st.expander("Message options"):
            confirm_remove(board, mid, member_id, prefix)

    if row["reply_count"]:
        with st.expander(f"Conversation · {row['reply_count']} {'reply' if row['reply_count']==1 else 'replies'}"):
            for reply in board.replies(mid, member_id):
                reply_unread = board.is_unread(reply["id"], member_id)
                post_card(reply, member_id, is_reply=True, is_unread=reply_unread)
                x, y, _ = st.columns([1.4, 2, 4])
                if reply_unread and x.button("✓ Mark read", key=f"rr_{prefix}_{reply['id']}"):
                    board.mark_seen(reply["id"], member_id)
                    st.rerun()
                if reply["author_id"] == member_id and not reply["deleted_at"]:
                    with y.popover("Options"):
                        confirm_remove(board, reply["id"], member_id, f"{prefix}_reply")
            if row["deleted_at"] is None and st.button("↩ Add a reply", key=f"add_reply_{prefix}_{mid}"):
                st.session_state["reply_to"] = mid
                st.rerun()
    st.write("")


@st.fragment(run_every=REFRESH_SECONDS)
def live_feed(board: Board, member_id: int, view: str, search: str, prefix: str) -> None:
    results = board.timeline(member_id, view=view, search=search, limit=70)
    if not results:
        st.info("Nothing here yet. Start with a note, or try a different filter.")
        return
    st.caption(f"Showing {len(results)} most recent conversation{'s' if len(results) != 1 else ''} · refreshes every {REFRESH_SECONDS}s")
    for row in results:
        show_thread(board, row, member_id, prefix)


@st.fragment(run_every=REFRESH_SECONDS)
def sidebar_stats(board: Board, member_id: int) -> None:
    st.metric("Unread messages", board.count_unread(member_id))
    st.caption(f"{len(board.members())} private seat(s) · updates every {REFRESH_SECONDS}s")


def main() -> None:
    st.markdown(STYLES, unsafe_allow_html=True)
    board = Board(config_value("BETWEEN_US_DB_PATH", "between_us.db"))
    member_id = entry(board)

    with st.sidebar:
        st.markdown("### ✉️ Between Us")
        st.caption("A quiet place to keep in touch.")
        st.divider()
        st.markdown(f"**Your seat:** Member {member_id:02d}")
        sidebar_stats(board, member_id)
        st.divider()
        st.caption("Keep your personal return key safe. Anyone with the link can join and read group posts.")
        if st.button("Sign out", use_container_width=True):
            clear_login()
            st.rerun()

    heading()
    show_new_key()
    reply_editor(board, member_id)

    tab_home, tab_write, tab_saved, tab_search = st.tabs(
        ["⌂ Conversations", "✍ Write", "☆ Saved", "⌕ Find & export"]
    )
    with tab_home:
        st.markdown('<div class="section-heading">Your conversations</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtitle">Everything you’re part of, with private letters kept between their participants.</div>', unsafe_allow_html=True)
        selection = st.radio("Show", ["All", "Unread", "Mine"],
                             horizontal=True, key="home_filter", label_visibility="collapsed")
        live_feed(board, member_id, selection, "", "home")

    with tab_write:
        compose(board, member_id)

    with tab_saved:
        st.markdown('<div class="section-heading">Keep close</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtitle">Conversations you bookmarked for another day.</div>', unsafe_allow_html=True)
        live_feed(board, member_id, "Saved", "", "saved")

    with tab_search:
        st.markdown('<div class="section-heading">Find your words</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtitle">Search the messages you are allowed to see, including replies.</div>', unsafe_allow_html=True)
        query = st.text_input("Search conversations", placeholder="Search titles, letters, and replies…")
        if query.strip():
            live_feed(board, member_id, "All", query, "search")
        st.divider()
        st.markdown("#### Your private copy")
        st.caption("Exports only conversations visible to your seat. A copy saved to your device is outside the app's control.")
        st.download_button("↓ Export visible messages (.txt)",
                           data=board.export_visible(member_id),
                           file_name="between-us-my-messages.txt", mime="text/plain")

    st.caption("Between Us · No shared passcode · Messages are stored on the app server, not end-to-end encrypted.")


if __name__ == "__main__":
    main()
