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
    lines = []

    # פתיחה
    lines.append("אהלן אח יקר 👋")
    lines.append(f"יאללה נראה איך היה {curr_label}")
    lines.append("")
    lines.append("")

    lines.append(f"📊 *סיכום חודשי — {curr_label} {curr_year}*")
    lines.append(f"_{prev_label} מול {curr_label}_")
    lines.append("")

    prev_cpl = prev["spend"] / prev["gifts"]    if prev["gifts"]    > 0 else 0
    curr_cpl = curr["spend"] / curr["gifts"]    if curr["gifts"]    > 0 else 0
    prev_cpc = prev["spend"] / prev["consults"] if prev["consults"] > 0 else 0
    curr_cpc = curr["spend"] / curr["consults"] if curr["consults"] > 0 else 0
    prev_cpt = prev["spend"] / prev["tx_count"] if prev["tx_count"] > 0 else 0
    curr_cpt = curr["spend"] / curr["tx_count"] if curr["tx_count"] > 0 else 0

    def row(emoji, label, p_num, c_num, lower_is_better=False, is_int=False):
        fmt = str if is_int else fmt_ils
        p_str = fmt(round(p_num)) if p_num else "—"
        c_str = fmt(round(c_num)) if c_num else "—"
        p     = pct(c_num, p_num)
        icon  = change_icon(c_num, p_num, lower_is_better)
        return f"{emoji} *{label}*\n`{prev_label}: {p_str}  |  {curr_label}: {c_str}  |  {p}{icon}`"

    lines.append(row("💰", "מחזור",          prev["revenue"],  curr["revenue"]))
    lines.append(row("📣", "תקציב פרסום",    prev["spend"],    curr["spend"]))
    lines.append(row("🤝", "עסקאות",         prev["tx_count"], curr["tx_count"], is_int=True))
    lines.append(row("📥", "עלות/ליד מתנה", prev_cpl,         curr_cpl,         lower_is_better=True))
    lines.append(row("🎯", "עלות/ייעוץ",     prev_cpc,         curr_cpc,         lower_is_better=True))
    lines.append(row("💸", "עלות/עסקה",      prev_cpt,         curr_cpt,         lower_is_better=True))
    lines.append("")
    lines.append("---")
    lines.append("")

    # תובנה
    rev_change = curr["revenue"] - prev["revenue"]
    tx_change  = curr["tx_count"] - prev["tx_count"]

    if rev_change > 0 and tx_change > 0:
        lines.append(f"💡 וואלה אחי — מחזור עלה ב-{fmt_ils(rev_change)} ועוד {tx_change} עסקאות. חודש חזק.")
    elif rev_change < 0 and tx_change < 0:
        lines.append(f"💡 תכלס, {curr_label} היה חלש יותר מ-{prev_label}. {fmt_ils(abs(rev_change))} פחות במחזור. תבדוק מה קרה.")
    elif curr_cpl > 0 and prev_cpl > 0 and curr_cpl < prev_cpl:
        lines.append(f"💡 עלות הליד ירדה — הפרסום עובד טוב יותר החודש. תמשיך ככה.")
    else:
        lines.append(f"💡 חודש יציב. תמשיך לעקוב אחרי עלות/עסקה.")

    lines.append("")
    lines.append("יאללה קדימה, חודש טוב 💪")

    return "\n".join(lines)

# ─────────────────────────────────────────
# שלח לטלגרם
# ─────────────────────────────────────────
def send_telegram(text):
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"}).encode()
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
