from __future__ import annotations

import os
import re
import sys
import time
import requests
from datetime import datetime, timedelta, timezone

MONDAY_API_TOKEN = os.environ['MONDAY_API_TOKEN']
SCHOOLER_CLIENT_ID = os.environ.get('SCHOOLER_CLIENT_ID', '')
SCHOOLER_CLIENT_SECRET = os.environ.get('SCHOOLER_CLIENT_SECRET', '')
SCHOOLER_USER_ID = os.environ.get('SCHOOLER_USER_ID', '')
TG_TOKEN = os.environ.get('TG_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')

MONDAY_BOARD_ID = 1722246362
SCHOOLER_BASE = 'https://api.schooler.biz'
GREEN_BASE = 'https://api.green-api.com'
STATE_FILE = '/tmp/last_student_check.txt'

# GREEN API instance per salesperson — each agent receives the alert on their own WhatsApp
SALES_AGENTS = {
    'אושרי דסטה': {'instance_id': '7103193002', 'token': '79b12afa3286491f9b3ef61723f4bfbf501e7b79b3654cfa84', 'self_chat_id': '972523733014@c.us'},
    'אופק ביטון':  {'instance_id': '7103363572', 'token': '80829464b03e4ca6bd51b1fe2296ec503e4ff131804a4dc1b4', 'self_chat_id': '972533783654@c.us'},
    'יוסף טהרני':  {'instance_id': '7103411404', 'token': 'b7b7f8ee783b4efc977c2a1136fe545e5339720e4f0d4eb996', 'self_chat_id': '972523733269@c.us'},
    'יובל סידיס':  {'instance_id': '7103363573', 'token': '2b06d8cf55cb4f2e8878bf99e4373ef1d8bb7ff8ae33417b8b', 'self_chat_id': '972523734216@c.us'},
    'נציג 2':       {'instance_id': '7103363573', 'token': '2b06d8cf55cb4f2e8878bf99e4373ef1d8bb7ff8ae33417b8b', 'self_chat_id': '972523734216@c.us'},
}


def get_monday_new_items(since: datetime) -> list:
    query = """
    query($board_id: ID!) {
        boards(ids: [$board_id]) {
            items_page(limit: 100, query_params: {order_by: [{column_id: "__creation_log__", direction: desc}]}) {
                items {
                    id
                    created_at
                    column_values(ids: ["text__1", "phone__1", "color_mkxsr8f9"]) {
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


def extract_columns(item: dict) -> tuple[str | None, str | None, str | None]:
    name = phone = agent = None
    for col in item['column_values']:
        if col['id'] == 'text__1':
            name = col['text'] or None
        elif col['id'] == 'phone__1':
            raw = col['text'] or ''
            phone = re.sub(r'[^\d+]', '', raw) or None
        elif col['id'] == 'color_mkxsr8f9':
            agent = col['text'] or None
    return name, phone, agent


def format_phone_for_whatsapp(phone: str) -> str:
    # Israeli number: 05XXXXXXXX → 9725XXXXXXXX@c.us
    digits = re.sub(r'[^\d]', '', phone)
    if digits.startswith('0'):
        digits = '972' + digits[1:]
    elif not digits.startswith('972'):
        digits = '972' + digits
    return f'{digits}@c.us'


def get_schooler_token() -> str:
    resp = requests.post(
        f'{SCHOOLER_BASE}/oauth/token',
        json={
            'grant_type': 'password',
            'client_id': SCHOOLER_CLIENT_ID,
            'client_secret': SCHOOLER_CLIENT_SECRET,
            'user_id': SCHOOLER_USER_ID,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()['access_token']


def search_student(token: str, phone: str, retries: int = 3, delay: int = 30) -> str | None:
    """Returns unique_link for the student if found, else None. Retries if not found yet."""
    for attempt in range(retries):
        resp = requests.get(
            f'{SCHOOLER_BASE}/api/v1/students/search',
            headers={'Authorization': f'Bearer {token}'},
            params={'phone': phone},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get('data', [])
        if data:
            schools = data[0].get('schools', [])
            if schools:
                return schools[0].get('unique_link') or None
        if attempt < retries - 1:
            print(f"  Not found yet, retrying in {delay}s... ({attempt + 1}/{retries - 1})")
            time.sleep(delay)
    return None


def send_telegram_not_in_schooler(student_name: str, phone: str, agent_name: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    message = f"⚠️ תלמיד חדש נרשם אבל לא נמצא בסקולר!\n*{student_name}* ({phone}) | {agent_name}\nבדוק ידנית."
    requests.post(
        f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
        json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'},
        timeout=15,
    )


def send_whatsapp(instance_id: str, token: str, self_chat_id: str, student_name: str, link: str) -> None:
    message = f"לינק ישיר לפורטל {student_name}:\n{link}"
    resp = requests.post(
        f'{GREEN_BASE}/waInstance{instance_id}/sendMessage/{token}',
        json={'chatId': self_chat_id, 'message': message},
        timeout=15,
    )
    resp.raise_for_status()


def send_telegram(student_name: str, agent_name: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    message = f"מתכלי תלמיד חדש נרשם! 🎉\n*{student_name}* | {agent_name}"
    requests.post(
        f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
        json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'},
        timeout=15,
    )


def load_last_check() -> datetime:
    lookback = os.environ.get('LOOKBACK_HOURS', '').strip()
    if lookback:
        return datetime.now(timezone.utc) - timedelta(hours=float(lookback))
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return datetime.fromisoformat(f.read().strip())
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

    if not SCHOOLER_CLIENT_ID:
        print("Schooler credentials not configured yet — skipping.")
        save_last_check(now)
        return

    print(f"Found {len(new_items)} new item(s). Getting Schooler token...")
    schooler_token = get_schooler_token()

    errors = []
    for item in new_items:
        item_id = item['id']
        name, phone, agent = extract_columns(item)

        if not name or not phone:
            msg = f"Item {item_id}: missing name or phone"
            print(msg); errors.append(msg); continue

        if not agent or agent not in SALES_AGENTS:
            msg = f"Item {item_id} ({name}): unknown agent '{agent}'"
            print(msg); errors.append(msg); continue

        print(f"Processing: {name} ({phone}) — נציג: {agent}")

        link = search_student(schooler_token, phone)
        if not link:
            print(f"{name}: still not found in Schooler after retries — sending Telegram alert")
            send_telegram_not_in_schooler(name, phone, agent)
            continue

        green = SALES_AGENTS[agent]
        send_whatsapp(green['instance_id'], green['token'], green['self_chat_id'], name, link)
        send_telegram(name, agent)
        print(f"WhatsApp sent to {agent} and Telegram sent about {name}")

    save_last_check(now)

    if errors:
        print(f"\n{len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
