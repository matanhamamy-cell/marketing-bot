#!/usr/bin/env python3
"""
סיכום חודשי — רץ ב-1 לכל חודש ב-09:00 שעון ישראל
משווה את החודש שנסגר מול החודש שלפניו
"""

import os, json, urllib.request, urllib.parse, urllib.error
from datetime import date

MONDAY_API_TOKEN          = os.environ["MONDAY_API_TOKEN"]
MONDAY_BOARD_ID           = os.environ["MONDAY_BOARD_ID"]
MONDAY_TRANSACTIONS_BOARD = os.environ["MONDAY_TRANSACTIONS_BOARD_ID"]
MONDAY_SOURCE_COL         = os.environ.get("MONDAY_SOURCE_COL", "status1__1")
MONDAY_DATE_COL           = os.environ.get("MONDAY_DATE_COL",   "date__1")
META_ACCESS_TOKEN         = os.environ["META_ACCESS_TOKEN"]
META_AD_ACCOUNT_ID        = os.environ["META_AD_ACCOUNT_ID"]
TG_TOKEN                  = os.environ["TG_TOKEN"]
TG_CHAT_ID                = os.environ["TG_CHAT_ID"]

TRANSACTIONS_DATE_COL   = "date5__1"
TRANSACTIONS_AMOUNT_COL = "numbers__1"

# ── תאריכים ──
today = date.today()

# החודש שזה עתה נגמר
curr_year  = today.year if today.month > 1 else today.year - 1
curr_month = today.month - 1 if today.month > 1 else 12
curr_start = date(curr_year, curr_month, 1).isoformat()
if curr_month == 12:
    curr_end = date(curr_year, 12, 31).isoformat()
else:
    curr_end = date(curr_year, curr_month + 1, 1).replace(day=1)
    curr_end = (date(curr_year, curr_month + 1, 1) - __import__('datetime').timedelta(days=1)).isoformat()

# החודש שלפניו
prev_year  = curr_year if curr_month > 1 else curr_year - 1
prev_month = curr_month - 1 if curr_month > 1 else 12
prev_start = date(prev_year, prev_month, 1).isoformat()
if prev_month == 12:
    prev_end = date(prev_year, 12, 31).isoformat()
else:
    prev_end = (date(curr_year if curr_month > 1 else curr_year, curr_month, 1) - __import__('datetime').timedelta(days=1)).isoformat()

MONTH_NAMES = {
    1:"ינואר",2:"פברואר",3:"מרץ",4:"אפריל",5:"מאי",6:"יוני",
    7:"יולי",8:"אוגוסט",9:"ספטמבר",10:"אוקטובר",11:"נובמבר",12:"דצמבר"
}
curr_label = MONTH_NAMES[curr_month]
prev_label = MONTH_NAMES[prev_month]

print(f"[INFO] Monthly summary: {prev_label} vs {curr_label}")
print(f"[INFO] Curr: {curr_start} → {curr_end}")
print(f"[INFO] Prev: {prev_start} → {prev_end}")

# ─────────────────────────────────────────
# Meta API
# ─────────────────────────────────────────
def meta_get(path, params):
    params["access_token"] = META_ACCESS_TOKEN
    url = f"https://graph.facebook.com/v19.0/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[WARN] Meta API error: {e}")
        return {"data": []}

def fetch_meta_spend(start, end):
    params = {
        "fields": "spend,clicks,cpc",
        "level": "account",
        "time_range": json.dumps({"since": start, "until": end}),
    }
    data = meta_get(f"{META_AD_ACCOUNT_ID}/insights", params)
    rows = data.get("data", [])
    if not rows:
        return 0.0
    return float(rows[0].get("spend", 0))

# ─────────────────────────────────────────
# Monday API
# ─────────────────────────────────────────
def monday_query(query):
    req = urllib.request.Request(
        "https://api.monday.com/v2",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json", "Authorization": MONDAY_API_TOKEN},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

def fetch_all_items(board_id, col_ids):
    items, cursor = [], None
    col_arg = ", ".join(f'"{c}"' for c in col_ids)
    while True:
        cursor_arg = f', cursor: "{cursor}"' if cursor else ""
        q = f"""query {{
          boards(ids: {board_id}) {{
            items_page(limit: 500{cursor_arg}) {{
              cursor
              items {{
                column_values(ids: [{col_arg}]) {{ id text }}
              }}
            }}
          }}
        }}"""
        data = monday_query(q)
        page = data["data"]["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
    return items

def fetch_transactions_period(start, end):
    items = fetch_all_items(MONDAY_TRANSACTIONS_BOARD,
                            [TRANSACTIONS_DATE_COL, TRANSACTIONS_AMOUNT_COL])
    count, revenue = 0, 0.0
    for item in items:
        vals = {c["id"]: (c["text"] or "").strip() for c in item["column_values"]}
        tx_date = vals.get(TRANSACTIONS_DATE_COL, "").split(" ")[0]
        try:
            amount = float((vals.get(TRANSACTIONS_AMOUNT_COL, "0") or "0").replace(",", ""))
        except ValueError:
            amount = 0.0
        if start <= tx_date <= end:
            count += 1
            revenue += amount
    return count, revenue

def fetch_leads_period(start, end):
    items = fetch_all_items(MONDAY_BOARD_ID, [MONDAY_SOURCE_COL, MONDAY_DATE_COL])
    gifts, consults = 0, 0
    for item in items:
        vals = {c["id"]: (c["text"] or "").strip() for c in item["column_values"]}
        source    = vals.get(MONDAY_SOURCE_COL, "")
        lead_date = vals.get(MONDAY_DATE_COL, "").split(" ")[0]
        if start <= lead_date <= end:
            if source == "מתנה חינמית":
                gifts += 1
            elif source == "מתנה חינמית - שיחת ייעוץ":
                consults += 1
    return gifts, consults

# ─────────────────────────────────────────
# בניית הודעה
# ─────────────────────────────────────────
def pct(new, old):
    if old == 0:
        return "—"
    p = (new - old) / old * 100
    return f"{'+' if p >= 0 else ''}{p:.1f}%"

def change_icon(new, old, lower_is_better=False):
    if old == 0:
        return ""
    improved = (new < old) if lower_is_better else (new > old)
    return " ✅" if improved else " ❌"

def fmt_ils(n):
    return f"₪{round(n):,}"

def build_message(curr, prev):
    prev_cpl = prev["spend"] / prev["gifts"]    if prev["gifts"]    > 0 else 0
    curr_cpl = curr["spend"] / curr["gifts"]    if curr["gifts"]    > 0 else 0
    prev_cpc = prev["spend"] / prev["consults"] if prev["consults"] > 0 else 0
    curr_cpc = curr["spend"] / curr["consults"] if curr["consults"] > 0 else 0
    prev_cpt = prev["spend"] / prev["tx_count"] if prev["tx_count"] > 0 else 0
    curr_cpt = curr["spend"] / curr["tx_count"] if curr["tx_count"] > 0 else 0

    def f(n, is_int=False):
        if not n: return "—"
        return str(round(n)) if is_int else f"₪{round(n):,}"

    def diff(c, p, lower_is_better=False):
        if not p: return ""
        d = (c - p) / p * 100
        better = (d < 0) if lower_is_better else (d > 0)
        sign = "+" if d >= 0 else ""
        icon = "✅" if better else "❌"
        return f"{sign}{d:.1f}% {icon}"

    rows = [
        ("מדד",                "ערך"),
        ("─" * 18,             "─" * 10),
        ("💰 מחזור חודשי",    f(curr["revenue"])),
        ("📣 תקציב כולל",     f(curr["spend"])),
        ("🤝 עסקאות",         str(curr["tx_count"])),
        ("📥 עלות/ליד",       f(curr_cpl)),
        ("🎯 עלות/ייעוץ",     f(curr_cpc)),
        ("💸 עלות/עסקה",      f(curr_cpt)),
    ]
    col0 = max(len(r[0]) for r in rows) + 2
    table = "\n".join(r[0].ljust(col0) + r[1] for r in rows)

    # תובנה
    if curr["revenue"] > prev["revenue"] and curr["tx_count"] > prev["tx_count"]:
        insight = f"וואלה אחי — חודש חזק. מחזור ועסקאות עלו. תמשיך ככה."
    elif curr["revenue"] < prev["revenue"] and curr["tx_count"] < prev["tx_count"]:
        insight = f"תכלס, {curr_label} היה חלש יותר מ-{prev_label}. תבדוק מה קרה ותתקן."
    elif curr_cpl > 0 and prev_cpl > 0 and curr_cpl < prev_cpl:
        insight = "עלות הליד ירדה — הפרסום עובד טוב יותר החודש. תמשיך ככה."
    else:
        insight = "חודש יציב. תמשיך לעקוב אחרי עלות/עסקה."

    msg = (
        f"אהלן אח יקר 👋\n"
        f"יאללה נראה איך היה {curr_label}\n\n\n"
        f"<b>📊 סיכום חודשי — {curr_label} {curr_year}</b>\n\n"
        f"<pre>{table}</pre>\n\n"
        f"💡 {insight}\n\n"
        f"יאללה קדימה, חודש טוב 💪"
    )
    return msg

# ─────────────────────────────────────────
# שלח לטלגרם
# ─────────────────────────────────────────
def send_telegram(text):
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        result = json.loads(r.read())
    if result.get("ok"):
        print("[INFO] Telegram message sent ✅")
    else:
        print(f"[ERROR] Telegram: {result}")

# ─────────────────────────────────────────
# Main
# ─────────────────────────────────────────
if __name__ == "__main__":
    print("[INFO] Fetching current month transactions...")
    curr_tx, curr_rev = fetch_transactions_period(curr_start, curr_end)
    print("[INFO] Fetching previous month transactions...")
    prev_tx, prev_rev = fetch_transactions_period(prev_start, prev_end)

    print("[INFO] Fetching current month leads...")
    curr_gifts, curr_consults = fetch_leads_period(curr_start, curr_end)
    print("[INFO] Fetching previous month leads...")
    prev_gifts, prev_consults = fetch_leads_period(prev_start, prev_end)

    print("[INFO] Fetching Meta spend...")
    curr_spend = fetch_meta_spend(curr_start, curr_end)
    prev_spend = fetch_meta_spend(prev_start, prev_end)

    curr = {"revenue": curr_rev, "tx_count": curr_tx, "gifts": curr_gifts, "consults": curr_consults, "spend": curr_spend}
    prev = {"revenue": prev_rev, "tx_count": prev_tx, "gifts": prev_gifts, "consults": prev_consults, "spend": prev_spend}

    print(f"[INFO] Curr: revenue={curr_rev}, tx={curr_tx}, gifts={curr_gifts}, consults={curr_consults}, spend={curr_spend}")
    print(f"[INFO] Prev: revenue={prev_rev}, tx={prev_tx}, gifts={prev_gifts}, consults={prev_consults}, spend={prev_spend}")

    msg = build_message(curr, prev)
    print("\n--- MESSAGE PREVIEW ---")
    print(msg)
    print("---\n")

    send_telegram(msg)
    print("[INFO] Done ✅")
