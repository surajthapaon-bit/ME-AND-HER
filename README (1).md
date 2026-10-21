# Between Us — No Shared Passcode

A polished, name-free note and letter board for small groups. Messages, replies, bookmarks, read status, search, and per-member exports are available without a shared room passphrase.

## Run locally

1. Install Python 3.10+.
2. Run `pip install -r requirements.txt`.
3. Run `streamlit run app.py`.
4. Open the app and choose **I'm new here** to create an anonymous seat. Save your personal **return key**. Use **I have a return key** to regain access when returning.
5. Share the app URL with your circle. **No Streamlit Secrets setup is needed.** The old `BOARD_ACCESS_CODE` secret is ignored and may be deleted.

## Important privacy tradeoff

**Anyone who knows or discovers the app URL can join and read every message addressed to Everyone, including older group posts.** Removing the shared passphrase makes group posts publicly accessible to anyone with the link. Do not put sensitive group messages here.

- Direct messages are visible only to the two participating seats (and potentially server/database administrators). Each seat uses an individual return key, which must be kept private.
- No personal names or usernames are required; members appear as Member 01, Member 02, etc.
- Replies inherit their root message's audience. Group-thread replies are visible to everyone who joins.
- Return keys are stored as SHA-256 digests rather than plaintext. Losing your key can permanently prevent you from recovering your seat and direct messages.
- This app is **not end-to-end encrypted** and has no invite approval, anti-spam controls, rate limiting, member revocation, or key recovery. The URL is **not** an access-control mechanism.

If you need a genuinely private group, enable invitation-based authentication rather than removing access controls.

## Streamlit Community Cloud

To update an existing deployment, replace the **`app.py` at the root of the linked GitHub repository** with the new `app.py`. You can keep your existing `storage.py` and `requirements.txt` unchanged; they are compatible. Updating the `README.md` is optional but recommended. Your Streamlit app URL stays the same when the linked repository and entrypoint are unchanged.

By default, data is written to `between_us.db`. **Streamlit Community Cloud storage is ephemeral and may be erased on reboot/redeployment.** For dependable shared use, move to persistent managed storage before creating important letters or notes. To set a persistent SQLite path on a suitable host, configure `BETWEEN_US_DB_PATH`.

Other optional settings: `APP_TIMEZONE` (defaults to `America/Toronto`) and `BETWEEN_US_DB_PATH` (defaults to `between_us.db`).

## Tests

Run `python -m unittest discover -s tests -v` from the project folder.
