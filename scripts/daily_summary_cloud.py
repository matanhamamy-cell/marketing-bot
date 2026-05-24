#!/usr/bin/env python3
"""
סיכום יומי — רץ ב-GitHub Actions כל יום ב-20:00 (שעון ישראל)
מושך נתוני עסקאות ממונדיי ושולח לטלגרם
"""

import os, json, urllib.request, urllib.parse
from datetime import date

MONDAY_API_TOKEN         = os.environ["MONDAY_API_TOKEN"]
MONDAY_BOARD_ID          = os.environ["MONDAY_BOARD_ID"]
MONDAY_TRANSACTIONS_BOARD= os.environ["MONDAY_TRANSACTIONS_BOARD_ID"]
MONDAY_SOURCE_COL        = os.environ.get("MONDAY_SOURCE_COL", "status1__1")
MONDAY_DATE_COL          = os.environ.get("MONDAY_DATE_COL",   "date__1")
TG_TOKEN                 = os.environ["TG_TOKEN"]
TG_CHAT_ID               = os.environ["TG_CHAT_ID"]

today      = date.today()
today_str  = today.isoformat()
start_date = today.replace(day=1).isoformat()

TRANSACTIONS_DATE_COL   = "date5__1"
TRANSACTIONS_AMOUNT_COL = "numbers__1"

print(f"[INFO] Daily summary for {today_str} (month from {start_date})")

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
                id
                column_values(ids: [{col_arg}]) {{ id text value }}
              }}
            }}
          }}
        }}"""
        data = monday_query(q)
        page  = data["data"]["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
    return items

# ─────────────────────────────────────────
# עסקאות
# ─────────────────────────────────────────
def fetch_transactions():
    print("[INFO] Fetching transactions board...")
    items = fetch_all_items(MONDAY_TRANSACTIONS_BOARD,
                            [TRANSACTIONS_DATE_COL, TRANSACTIONS_AMOUNT_COL])
    today_tx, month_tx = [], []
    today_rev, month_rev = 0.0, 0.0

    for item in items:
        vals = {c["id"]: (c["text"] or "").strip() for c in item["column_values"]}
        raw_date   = vals.get(TRANSACTIONS_DATE_COL, "")
        raw_amount = vals.get(TRANSACTIONS_AMOUNT_COL, "0") or "0"
        tx_date    = raw_date.split(" ")[0] if raw_date else ""
        try:
            amount = float(raw_amount.replace(",", ""))
        except ValueError:
            amount = 0.0

        if tx_date == today_str:
            today_tx.append(amount)
            today_rev += amount
        if start_date <= tx_date <= today_str:
            month_tx.append(amount)
            month_rev += amount

    print(f"[INFO] Transactions — today: {len(today_tx)}, month: {len(month_tx)}")
    return {
        "today_count":  len(today_tx),
        "month_count":  len(month_tx),
        "today_rev":    today_rev,
        "month_rev":    month_rev,
    }

# ─────────────────────────────────────────
# לידים היום
# ─────────────────────────────────────────
def fetch_leads_today():
    print("[INFO] Fetching leads board...")
    items = fetch_all_items(MONDAY_BOARD_ID, [MONDAY_SOURCE_COL, MONDAY_DATE_COL])

    gift, consult, bio, total = 0, 0, 0, 0

    for item in items:
        vals   = {c["id"]: (c["text"] or "").strip() for c in item["column_values"]}
        source = vals.get(MONDAY_SOURCE_COL, "")
        raw_dt = vals.get(MONDAY_DATE_COL, "")
        lead_date = raw_dt.split(" ")[0] if raw_dt else ""

        if lead_date != today_str:
            continue

        total += 1
        if source == "מתנה חינמית":
            gift += 1
        elif source == "מתנה חינמית - שיחת ייעוץ":
            consult += 1
        elif "ביו" in source or "instagram" in source.lower() or "אינסטגרם" in source:
            bio += 1

    print(f"[INFO] Leads today — gift: {gift}, consult: {consult}, bio: {bio}, total: {total}")
    return {"gift": gift, "consult": consult, "bio": bio, "total": total}

# ─────────────────────────────────────────
# בניית הודעה
# ─────────────────────────────────────────
def build_message(tx, leads):
    d = f"{today_str[8:10]}/{today_str[5:7]}"

    # פתיחה לפי ביצועים
    lines = []
    if tx["today_count"] >= 3:
        lines.append("וואלה אחי מתן 🔥")
        lines.append("יום חזק היום — נראה את המספרים")
    else:
        lines.append("אהלן אח יקר 👋")
        lines.append("יאללה נראה איפה אנחנו היום")
    lines.append("")
    lines.append("")
    lines.append(f"📅 *סיכום יומי — {d}*")
    lines.append("")

    # מחזור
    lines.append("💰 *מחזור*")
    lines.append(f"חודשי: ₪{round(tx['month_rev']):,}")
    lines.append(f"היום: ₪{round(tx['today_rev']):,}")
    lines.append("")

    # עסקאות
    lines.append("🤝 *עסקאות*")
    lines.append(f"חודשי: {tx['month_count']}")
    lines.append(f"היום: {tx['today_count']}")
    lines.append("")

    # לידים
    lines.append("📥 *לידים היום*")
    lines.append(f"מתנה: {leads['gift']}")
    lines.append(f"ייעוץ: {leads['consult']}")
    lines.append(f"דף ביו אינסטגרם: {leads['bio']}")
    lines.append(f"סה\"כ: {leads['total']}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # תובנה
    lines.append("⚡ *תובנה יומית*")
    if tx["today_count"] >= 3:
        lines.append("וואלה אחי, יום טוב. תמשיך ככה.")
    elif tx["today_count"] >= 1 and leads["total"] >= 5:
        lines.append("יש לידים, יש עסקאות — הגלגל מסתובב. תמשיך לדחוף.")
    elif tx["today_count"] == 0 and leads["total"] >= 5:
        lines.append("לידים יש, עסקאות אין — בוא נסגור יותר. מה עוצר אותנו?")
    elif tx["today_count"] == 0 and leads["total"] == 0:
        lines.append("אפס עסקאות, אפס לידים היום. חאלס, מחר תפצה.")
    else:
        lines.append("תכלס לא כל יום יהיה מטורף — אבל תבדוק מה אפשר לדחוף.")

    lines.append("")
    lines.append("יאללה קדימה, תעשה מה שצריך 💪")

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
    tx    = fetch_transactions()
    leads = fetch_leads_today()
    msg   = build_message(tx, leads)
    print("\n--- MESSAGE PREVIEW ---")
    print(msg)
    print("---\n")
    send_telegram(msg)
    print("[INFO] Done ✅")
