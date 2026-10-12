"""Weekly Cardcom -> Monday collection board sync.

Reads failed recurring charges from Cardcom (API v11, read-only) and mirrors *new* collection
problems into Monday board "טבלת גבייה לקוחות". Hila handles everything after that by hand —
this script never closes, moves to burned, or touches statuses she set.

Rules (agreed with Matan 2026-10-11):
- debtor not on the board            -> create item in "גביות פתוחות" (מצב פנייה = חדש)
- on the board in an open group      -> only raise סה"כ חוב if more charges failed since
- in "גביות שנסגרו"                  -> reopen only if a charge failed after the item's תאריך חוב
- in "גביות שנשרפו"                  -> never touch
A debtor = account with at least one recurring charge still in DEBTAUTOBILLING.

SYNC_MODE=dry (default) only reports to Telegram; SYNC_MODE=live writes to Monday.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

CARDCOM_API_NAME = os.environ['CARDCOM_API_NAME']
CARDCOM_API_PASSWORD = os.environ['CARDCOM_API_PASSWORD']
MONDAY_API_TOKEN = os.environ['MONDAY_API_TOKEN']
TG_TOKEN = os.environ.get('TG_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')
SYNC_MODE = (os.environ.get('SYNC_MODE') or 'dry').strip().lower()
FORCE = (os.environ.get('FORCE') or '').strip().lower() in ('1', 'true', 'yes')

CARDCOM_BASE = 'https://secure.cardcom.solutions/api/v11'
DEBT_STATUSES = {'DEBTAUTOBILLING'}
FAILURE_LOOKBACK_DAYS = 45   # window for finding accounts with failed charges
HISTORY_LOOKBACK_DAYS = 180  # window for summing an account's unpaid charges

BOARD_ID = 5090562656
BOARD_URL = 'https://matanhamamys-team.monday.com/boards/5090562656/pulses/'
GROUP_OPEN = 'topics'                  # גביות פתוחות
OPEN_GROUPS = {GROUP_OPEN, 'group_mm3e7h38', 'group_mm2fk7m9', 'group_mm1q8adb'}
GROUP_CLOSED = 'group_mkzvn338'        # גביות שנסגרו
GROUP_BURNED = 'group_mm0qvqba'        # גביות שנשרפו

COL_NAME = 'text_mkzvmag1'
COL_PHONE = 'text_mkzvnzmp'
COL_DATE = 'date_mkzv8y9x'
COL_AMOUNT = 'numeric_mkzvbkc8'
COL_STATE = 'color_mkzvagy3'           # מצב פנייה
COL_ACCOUNT = 'text_mm817b52'          # מספר חשבון קארדקום
ITEM_NAME = 'גבייה'                    # matches how items are named by hand

STATE_FILE = 'data/.cardcom-sync-last-run'
IL_TZ = timezone(timedelta(hours=3))


# ---------- Cardcom ----------

def cardcom(path: str, body: dict, method: str = 'POST') -> dict:
    # GetRecurringPaymentHistory only accepts GET with a JSON body — requests can send that
    resp = requests.request(method, f'{CARDCOM_BASE}{path}', json=body, timeout=60)
    data = resp.json()
    if data.get('ResponseCode') not in (0, None):
        raise RuntimeError(f'Cardcom {path}: {data.get("ResponseCode")} {data.get("Description")}')
    return data


def ddmmyyyy(d) -> str:
    return d.strftime('%d%m%Y')


def failed_accounts(today) -> set[int]:
    since = today - timedelta(days=FAILURE_LOOKBACK_DAYS)
    accounts, page = set(), 1
    while True:
        data = cardcom('/Transactions/ListTransactions', {
            'ApiName': CARDCOM_API_NAME, 'ApiPassword': CARDCOM_API_PASSWORD,
            'FromDate': ddmmyyyy(since), 'ToDate': ddmmyyyy(today),
            'TranStatus': 'Failure', 'Page': page, 'Page_size': 2000,
        })
        rows = data.get('Tranzactions') or []
        accounts.update(r['AccountId'] for r in rows if r.get('AccountId'))
        if len(rows) < 2000:
            return accounts
        page += 1


def account_debt(account_id: int, today) -> dict | None:
    data = cardcom('/RecuringPayments/GetRecurringPaymentHistory', {
        'apiUserName': CARDCOM_API_NAME, 'apiPassword': CARDCOM_API_PASSWORD,
        'AccountId': account_id,
        'FromDate': ddmmyyyy(today - timedelta(days=HISTORY_LOOKBACK_DAYS)), 'ToDate': ddmmyyyy(today),
    }, method='GET')
    unpaid = [r for r in data.get('RecurringPaymentHistory') or [] if r.get('Status') in DEBT_STATUSES]
    if not unpaid:
        return None
    acc = cardcom('/Account/GetByAccountId', {
        'ApiName': CARDCOM_API_NAME, 'ApiPassword': CARDCOM_API_PASSWORD, 'AccountId': account_id,
    }).get('Account') or {}
    first, last = (acc.get('FirstName') or '').strip(), (acc.get('Name') or '').strip()
    name = last if first and first in last else f'{first} {last}'.strip()
    dates = sorted(r['CreateDate'][:10] for r in unpaid)
    return {
        'account_id': account_id,
        'name': name,
        'phone': (acc.get('Mobile') or acc.get('Phone') or '').strip(),
        'debt': round(sum(r.get('SumToBill') or 0 for r in unpaid)),
        'newest': dates[-1],
    }


# ---------- Monday ----------

def monday(query: str, variables: dict | None = None) -> dict:
    resp = requests.post('https://api.monday.com/v2',
                         headers={'Authorization': MONDAY_API_TOKEN, 'Content-Type': 'application/json',
                                  'API-Version': '2024-10'},
                         json={'query': query, 'variables': variables or {}}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if 'errors' in data:
        raise RuntimeError(f'Monday: {data["errors"]}')
    return data['data']


def board_items() -> list[dict]:
    cols = f'["{COL_NAME}","{COL_PHONE}","{COL_DATE}","{COL_AMOUNT}","{COL_STATE}","{COL_ACCOUNT}"]'
    fields = f'cursor items {{ id group {{ id }} column_values(ids: {cols}) {{ id text }} }}'
    page = monday(f'query {{ boards(ids: [{BOARD_ID}]) {{ items_page(limit: 200) {{ {fields} }} }} }}')
    page = page['boards'][0]['items_page']
    items = []
    while True:
        for it in page['items']:
            cv = {c['id']: c['text'] or '' for c in it['column_values']}
            items.append({'id': it['id'], 'group': it['group']['id'], **cv})
        if not page['cursor']:
            return items
        page = monday(f'query($c: String!) {{ next_items_page(limit: 200, cursor: $c) {{ {fields} }} }}',
                      {'c': page['cursor']})['next_items_page']


def norm_phone(raw: str) -> str:
    digits = re.sub(r'\D', '', raw or '')
    if digits.startswith('972'):
        digits = '0' + digits[3:]
    elif len(digits) == 9 and not digits.startswith('0'):
        digits = '0' + digits
    return digits


def norm_name(raw: str) -> str:
    return re.sub(r'\s+', '', raw or '')


def find_item(debtor: dict, items: list[dict]) -> tuple[dict | None, str]:
    acc, phone, name = str(debtor['account_id']), norm_phone(debtor['phone']), norm_name(debtor['name'])
    for how, match in (
        ('account', lambda it: it[COL_ACCOUNT].strip() == acc),
        ('phone', lambda it: phone and norm_phone(it[COL_PHONE]) == phone),
        ('name', lambda it: name and norm_name(it[COL_NAME]) == name),
    ):
        hits = [it for it in items if match(it)]
        if hits:
            # prefer an open item, then closed, then burned; latest debt date first
            rank = {**{g: 0 for g in OPEN_GROUPS}, GROUP_CLOSED: 1, GROUP_BURNED: 2}
            hits.sort(key=lambda it: it[COL_DATE], reverse=True)
            hits.sort(key=lambda it: rank.get(it['group'], 3))
            return hits[0], how
    return None, ''


def set_columns(item_id: str, values: dict) -> None:
    monday('mutation($i: ID!, $v: JSON!) { change_multiple_column_values(item_id: $i, board_id: %d, '
           'column_values: $v) { id } }' % BOARD_ID, {'i': item_id, 'v': json_dumps(values)})


def add_update(item_id: str, body: str) -> None:
    monday('mutation($i: ID!, $b: String!) { create_update(item_id: $i, body: $b) { id } }',
           {'i': item_id, 'b': body})


def create_item(values: dict) -> str:
    data = monday('mutation($v: JSON!) { create_item(board_id: %d, group_id: "%s", item_name: "%s", '
                  'column_values: $v) { id } }' % (BOARD_ID, GROUP_OPEN, ITEM_NAME), {'v': json_dumps(values)})
    return data['create_item']['id']


def json_dumps(v: dict) -> str:
    return json.dumps(v, ensure_ascii=False)


# ---------- Telegram ----------

def telegram(text: str) -> None:
    if not (TG_TOKEN and TG_CHAT_ID):
        print(text)
        return
    for i in range(0, len(text), 3900):
        requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                      json={'chat_id': TG_CHAT_ID, 'text': text[i:i + 3900], 'disable_web_page_preview': True},
                      timeout=30)


# ---------- main ----------

def plan(debtors: list[dict], items: list[dict], today_iso: str) -> list[dict]:
    actions = []
    for d in debtors:
        item, how = find_item(d, items)
        base = {'debtor': d, 'item': item, 'how': how}
        if item is None:
            actions.append({**base, 'kind': 'create'})
        elif item['group'] == GROUP_BURNED:
            actions.append({**base, 'kind': 'skip', 'why': 'נשרף'})
        elif item['group'] == GROUP_CLOSED:
            if not item[COL_DATE] or d['newest'] > item[COL_DATE]:
                actions.append({**base, 'kind': 'reopen'})
            else:
                actions.append({**base, 'kind': 'skip', 'why': 'נסגר אחרי החיוב האחרון'})
        else:
            old = float(item[COL_AMOUNT] or 0)
            if d['debt'] > old:
                actions.append({**base, 'kind': 'amount', 'old': old})
            else:
                actions.append({**base, 'kind': 'skip', 'why': 'כבר פתוח'})
        # always remember the Cardcom account on a matched item (column is ours, not Hila's)
        if item is not None and not item[COL_ACCOUNT].strip():
            actions[-1]['backfill'] = True
    return actions


def apply(a: dict, today_iso: str) -> None:
    d, item = a['debtor'], a['item']
    note = '🤖 סנכרון קארדקום: '
    if a['kind'] == 'create':
        a['item_id'] = create_item({
            COL_NAME: d['name'], COL_PHONE: d['phone'], COL_DATE: {'date': today_iso},
            COL_AMOUNT: str(d['debt']), COL_STATE: {'label': 'חדש'}, COL_ACCOUNT: str(d['account_id']),
        })
        add_update(a['item_id'], note + f'חיוב מ-{d["newest"]} לא עבר (הוראת קבע). סה"כ חוב פתוח: {d["debt"]}₪')
        return
    a['item_id'] = item['id']
    if a['kind'] == 'reopen':
        set_columns(item['id'], {COL_STATE: {'label': 'חדש'}, COL_DATE: {'date': today_iso},
                                 COL_AMOUNT: str(d['debt']), COL_ACCOUNT: str(d['account_id'])})
        add_update(item['id'], note + f'נפתח מחדש – חיוב חדש מ-{d["newest"]} לא עבר. סה"כ חוב: {d["debt"]}₪')
    elif a['kind'] == 'amount':
        set_columns(item['id'], {COL_AMOUNT: str(d['debt']), COL_ACCOUNT: str(d['account_id'])})
        add_update(item['id'], note + f'חיוב נוסף לא עבר ({d["newest"]}). סכום עודכן {a["old"]:g}→{d["debt"]}₪')
    elif a.get('backfill'):
        set_columns(item['id'], {COL_ACCOUNT: str(d['account_id'])})


def report(actions: list[dict], live: bool, accounts_checked: int) -> str:
    def line(a):
        d = a['debtor']
        s = f'• {d["name"]} ({d["phone"] or "אין טלפון"}) – {d["debt"]}₪'
        if a['kind'] == 'reopen':
            s += f' | חיוב חדש {d["newest"]}'
        if a['kind'] == 'amount':
            s += f' | היה {a["old"]:g}₪'
        if a['kind'] == 'skip':
            s += f' | {a["why"]}'
        if a.get('how') in ('phone', 'name'):
            s += f' | זוהה לפי {"טלפון" if a["how"] == "phone" else "שם ⚠️"}'
        if live and a.get('item_id'):
            s += f'\n  {BOARD_URL}{a["item_id"]}'
        return s

    head = ('✅ סנכרון גבייה קארדקום – בוצע' if live
            else '🔍 סנכרון גבייה קארדקום – מצב בדיקה (לא בוצע כלום במאנדיי)')
    verb = {'create': 'נוצרו' if live else 'הייתי יוצר', 'reopen': 'נפתחו מחדש' if live else 'הייתי פותח מחדש',
            'amount': 'עודכן סכום' if live else 'הייתי מעדכן סכום', 'skip': 'בלי שינוי'}
    icon = {'create': '➕', 'reopen': '🔄', 'amount': '💰', 'skip': '⏭'}
    parts = [head, f'נבדקו {accounts_checked} חשבונות עם חיוב שנכשל, נמצאו {len(actions)} חייבים פעילים.']
    for kind in ('create', 'reopen', 'amount', 'skip'):
        group = [a for a in actions if a['kind'] == kind]
        if group:
            parts.append(f'\n{icon[kind]} {verb[kind]} ({len(group)}):\n' + '\n'.join(line(a) for a in group))
    if not any(a['kind'] != 'skip' for a in actions):
        parts.append('\nאין בעיות גבייה חדשות השבוע ✅')
    return '\n'.join(parts)


def main() -> None:
    now = datetime.now(IL_TZ)
    today, today_iso = now.date(), now.date().isoformat()
    live = SYNC_MODE == 'live'

    if not FORCE and os.path.exists(STATE_FILE) and open(STATE_FILE).read().strip() == today_iso:
        print(f'Already ran today ({today_iso}), skipping')
        return

    accounts = failed_accounts(today)
    debtors = [d for d in (account_debt(a, today) for a in sorted(accounts)) if d and d['debt'] > 0]
    items = board_items()
    actions = plan(debtors, items, today_iso)

    if live:
        for a in actions:
            if a['kind'] != 'skip' or a.get('backfill'):
                apply(a, today_iso)

    telegram(report(actions, live, len(accounts)))

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        f.write(today_iso + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # noqa: BLE001 — any failure must reach Matan, never fail silently
        telegram(f'❌ סנכרון גבייה קארדקום נכשל:\n{type(e).__name__}: {e}')
        raise
