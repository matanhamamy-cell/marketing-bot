#!/usr/bin/env python3
"""
דוח חודשי לקהילת הווצאפ — רץ ב-1 לכל חודש ב-09:00 שעון ישראל.
שולף מספר קליקים מ-Bitly עבור כל הלינקים שנוצרו בחודש שחלף (מתוך
scripts/community_links_log.csv), ושולח סיכום קצר למתן דרך בוט
"הקהילה של מתן חממי" — בסיס לשיחת המדדים החודשית המשותפת.
"""

import csv
import json
import os
import urllib.error
import urllib.request
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LINKS_LOG = os.path.join(SCRIPT_DIR, "community_links_log.csv")

BITLY_ACCESS_TOKEN = os.environ.get("BITLY_ACCESS_TOKEN", "")
COMMUNITY_BOT_TOKEN = os.environ["COMMUNITY_BOT_TOKEN"]
COMMUNITY_CHAT_ID = os.environ["COMMUNITY_CHAT_ID"]


def previous_month_range() -> tuple[int, int]:
    today = date.today()
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def load_links_for_month(year: int, month: int) -> list[dict]:
    if not os.path.exists(LINKS_LOG):
        return []
    rows = []
    with open(LINKS_LOG, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row_date = date.fromisoformat(row["date"])
            if row_date.year == year and row_date.month == month:
                rows.append(row)
    return rows


def get_clicks(link_id: str) -> int:
    if not BITLY_ACCESS_TOKEN or not link_id:
        return 0
    req = urllib.request.Request(
        f"https://api-ssl.bitly.com/v4/bitlinks/{link_id}/clicks/summary?unit=month&units=1",
        headers={"Authorization": f"Bearer {BITLY_ACCESS_TOKEN}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
        return result.get("total_clicks", 0)
    except urllib.error.URLError as e:
        print(f"[WARN] Bitly clicks fetch failed for {link_id}: {e}")
        return 0


def build_message(year: int, month: int, rows: list[dict]) -> str:
    month_label = f"{month:02d}/{year}"
    if not rows:
        return (
            f"📊 <b>דוח חודשי — קהילת ווצאפ — {month_label}</b>\n\n"
            f"לא נשלחו קריאות לפעולה עם לינק החודש, אז אין קליקים לדווח.\n\n"
            f"בוא נדבר איך רוצים להמשיך מכאן 💬"
        )

    total_clicks = 0
    lines = []
    for row in rows:
        clicks = get_clicks(row["link_id"])
        total_clicks += clicks
        lines.append(f"• {row['date']} — {clicks} קליקים ({row['short_link']})")

    details = "\n".join(lines)
    return (
        f"📊 <b>דוח חודשי — קהילת ווצאפ — {month_label}</b>\n\n"
        f"סה\"כ {len(rows)} קריאות לפעולה נשלחו החודש, עם {total_clicks} קליקים במצטבר:\n\n"
        f"{details}\n\n"
        f"בוא נשב על זה ונחליט מה לחדד לחודש הבא 💪"
    )


def send_telegram(text: str) -> None:
    payload = json.dumps({
        "chat_id": COMMUNITY_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{COMMUNITY_BOT_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        result = json.loads(r.read())
    if result.get("ok"):
        print("[INFO] Telegram message sent ✅")
    else:
        print(f"[ERROR] Telegram: {result}")


def main() -> None:
    year, month = previous_month_range()
    rows = load_links_for_month(year, month)
    message = build_message(year, month, rows)

    print("\n--- MESSAGE PREVIEW ---")
    print(message)
    print("---\n")

    send_telegram(message)
    print("[INFO] Done ✅")


if __name__ == "__main__":
    main()
