import os
import re
import sys
import requests
from datetime import datetime, timedelta, timezone

MONDAY_API_TOKEN = os.environ['MONDAY_API_TOKEN']
SCHOOLER_API_KEY = os.environ['SCHOOLER_API_KEY']
TG_TOKEN = os.environ['TG_TOKEN']
TG_CHAT_ID = os.environ['TG_CHAT_ID']

MONDAY_BOARD_ID = 1722246362
SCHOOLER_BASE = 'https://api.schooler.biz'
STATE_FILE = '/tmp/last_student_check.txt'


def get_monday_new_items(since: datetime) -> list:
    query = """
    query($board_id: ID!) {
        boards(ids: [$board_id]) {
            items_page(limit: 100) {
                items {
                    id
                    created_at
                    column_values(ids: ["text__1", "phone__1"]) {
                        id
                        text
                    }
                }
            }
        }
    }
    """
    resp = requests.post(
        'https://api.monday.com/v2',
        headers={'Authorization': MONDAY_API_TOKEN, 'Content-Type': 'application/json'},
        json={'query': query, 'variables': {'board_id': str(MONDAY_BOARD_ID)}},
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json()['data']['boards'][0]['items_page']['items']

    new_items = []
    for item in items:
        created_at = datetime.fromisoformat(item['created_at'].replace('Z', '+00:00'))
        if created_at > since:
            new_items.append(item)
    return new_items


def extract_columns(item: dict) -> tuple[str | None, str | None]:
    name = phone = None
    for col in item['column_values']:
        if col['id'] == 'text__1':
            name = col['text'] or None
        elif col['id'] == 'phone__1':
            # normalize: keep digits and leading +
            raw = col['text'] or ''
            phone = re.sub(r'[^\d+]', '', raw) or None
    return name, phone


def schooler_headers() -> dict:
    return {'Authorization': f'Bearer {SCHOOLER_API_KEY}'}


def search_student(phone: str) -> str | None:
    resp = requests.get(
        f'{SCHOOLER_BASE}/api/v1/students/search',
        headers=schooler_headers(),
        params={'phone': phone},
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json()
    if isinstance(results, list) and results:
        return str(results[0]['id'])
    return None


def get_unique_link(student_id: str) -> str | None:
    resp = requests.get(
        f'{SCHOOLER_BASE}/api/v1/students/{student_id}/unique_link',
        headers=schooler_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get('unique_link')


def send_telegram(name: str, link: str) -> None:
    text = f"\U0001f393 תלמיד חדש נרשם\!\n\n*{name}*\n[כניסה לפורטל]({link})"
    requests.post(
        f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
        json={
            'chat_id': TG_CHAT_ID,
            'text': text,
            'parse_mode': 'MarkdownV2',
            'disable_web_page_preview': False,
        },
        timeout=15,
    ).raise_for_status()


def load_last_check() -> datetime:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return datetime.fromisoformat(f.read().strip())
    # first run: look back 10 minutes to avoid missing anything
    return datetime.now(timezone.utc) - timedelta(minutes=10)


def save_last_check(ts: datetime) -> None:
    with open(STATE_FILE, 'w') as f:
        f.write(ts.isoformat())


def main() -> None:
    last_check = load_last_check()
    now = datetime.now(timezone.utc)

    print(f"Checking for new students since {last_check.isoformat()}")

    new_items = get_monday_new_items(since=last_check)
    if not new_items:
        print("No new items.")
        save_last_check(now)
        return

    print(f"Found {len(new_items)} new item(s).")

    errors = []
    for item in new_items:
        item_id = item['id']
        name, phone = extract_columns(item)

        if not name or not phone:
            msg = f"Item {item_id}: missing name or phone (name={name}, phone={phone})"
            print(msg)
            errors.append(msg)
            continue

        print(f"Processing: {name} ({phone})")

        student_id = search_student(phone)
        if not student_id:
            msg = f"Student not found in Schooler for phone {phone}"
            print(msg)
            errors.append(msg)
            continue

        link = get_unique_link(student_id)
        if not link:
            msg = f"No unique_link returned for student {student_id}"
            print(msg)
            errors.append(msg)
            continue

        send_telegram(name, link)
        print(f"Sent Telegram for {name}")

    save_last_check(now)

    if errors:
        print(f"\n{len(errors)} error(s) encountered:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
