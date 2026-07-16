from __future__ import annotations

import email
import imaplib
import os
import re
import sys
from email.header import decode_header

import requests

GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']
SALES_BOT_TOKEN = os.environ.get('SALES_BOT_TOKEN', '')
SALES_CHAT_ID = os.environ.get('SALES_CHAT_ID', '')

IMAP_HOST = 'imap.gmail.com'
SENDER = 'powerdoc@powerdoc.co.il'
SUBJECT_PREFIX = 'הגיע מסמך חוזה'
NAME_RE = re.compile(r'חתום\s*מ(.+)$')
STATE_FILE = '/tmp/last_contract_uid.txt'


def decode_subject(raw: str) -> str:
    parts = decode_header(raw)
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or 'utf-8', errors='replace'))
        else:
            out.append(text)
    return ''.join(out)


def load_last_uid(imap: imaplib.IMAP4_SSL) -> int:
    lookback = os.environ.get('LOOKBACK_ALL', '').strip()
    if lookback:
        return 0
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return int(f.read().strip())
    # First run: skip historical mail, start from the current top of the mailbox.
    typ, data = imap.uid('search', None, 'ALL')
    uids = data[0].split() if data and data[0] else []
    return int(uids[-1]) if uids else 0


def save_last_uid(uid: int) -> None:
    with open(STATE_FILE, 'w') as f:
        f.write(str(uid))


def get_new_signed_contracts(imap: imaplib.IMAP4_SSL, since_uid: int) -> list[tuple[int, str]]:
    typ, data = imap.uid(
        'search', None,
        'UID', f'{since_uid + 1}:*',
        'FROM', f'"{SENDER}"',
    )
    if typ != 'OK' or not data or not data[0]:
        return []

    results = []
    for uid_bytes in data[0].split():
        uid = int(uid_bytes)
        if uid <= since_uid:
            continue
        typ, msg_data = imap.uid('fetch', uid_bytes, '(BODY.PEEK[HEADER.FIELDS (SUBJECT)])')
        if typ != 'OK' or not msg_data or not msg_data[0]:
            continue
        raw_header = msg_data[0][1]
        subject = decode_subject(email.message_from_bytes(raw_header).get('Subject', ''))
        if subject.startswith(SUBJECT_PREFIX):
            results.append((uid, subject))
    return results


def extract_name(subject: str) -> str | None:
    match = NAME_RE.search(subject)
    if not match:
        return None
    return match.group(1).strip()


def send_signed_alert(name: str) -> None:
    if not SALES_BOT_TOKEN or not SALES_CHAT_ID:
        return
    text = f"הלווווו\n*{name}* חתם על טופס ההרשמה\nאפשר להתקדם ✅"
    requests.post(
        f'https://api.telegram.org/bot{SALES_BOT_TOKEN}/sendMessage',
        json={'chat_id': SALES_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'},
        timeout=15,
    )


def main() -> None:
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    imap.select('INBOX', readonly=True)

    last_uid = load_last_uid(imap)
    print(f"Checking for signed contracts since UID {last_uid}")

    matches = get_new_signed_contracts(imap, last_uid)
    imap.logout()

    if not matches:
        print("No new signed contracts.")
        save_last_uid(last_uid)
        return

    max_uid = last_uid
    errors = []
    for uid, subject in matches:
        max_uid = max(max_uid, uid)
        name = extract_name(subject)
        if not name:
            msg = f"UID {uid}: could not extract name from subject: {subject!r}"
            print(msg); errors.append(msg); continue
        print(f"Signed: {name}")
        send_signed_alert(name)

    save_last_uid(max_uid)

    if errors:
        print(f"\n{len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
