#!/usr/bin/env python3
"""
סיסטם בשורות טובות — שולח ביום האחרון של כל חודש.
מחולק לפי נציג: ביטלו + לא סגר.
"""
import os, json, sys, urllib.request
from calendar import monthrange
from datetime import date, datetime, timezone

MONDAY_API_TOKEN = os.environ["MONDAY_API_TOKEN"]
TG_TOKEN         = os.environ["TG_TOKEN"]
TG_CHAT_ID       = os.environ["TG_CHAT_ID"]

LEADS_BOARD_ID        = 5074806440
TRANSACTIONS_BOARD_ID = 1722246362

LEADS_STATUS_COL   = "project_status"
LEADS_AGENT_COL    = "color_mkx9tss"
LO_SAGAR_INDEX     = 2  # "לא סגר"

CANCEL_COL         = "color_mm1jmx15"
CANCEL_AGENT_COL   = "color_mkxsr8f9"
AMOUNT_COL         = "numbers__1"
CANCEL_LABEL       = "ביטל"
CANCEL_THRESHOLD   = 500.0

MONTH_NAMES = {
    1:"ינואר", 2:"פברואר", 3:"מרץ",  4:"אפריל",
    5:"מאי",   6:"יוני",   7:"יולי", 8:"אוגוסט",
    9:"ספטמבר",10:"אוקטובר",11:"נובמבר",12:"דצמבר",
}
MONTH_GROUPS = {
    1: "new_group29179",    2: "new_group43041",
    3: "new_group__1",      4: "new_group34887__1",
    5: "new_group20254__1", 6: "new_group96902__1",
    7: "new_group61115__1", 8: "new_group36909__1",
    9: "new_group58900__1", 10:"new_group38832__1",
    11:"new_group51853__1", 12:"new_group77615__1",
}

today         = date.today()
last_day      = monthrange(today.year, today.month)[1]
month_label   = MONTH_NAMES[today.month]
month_start   = datetime(today.year, today.month, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
current_group = MONTH_GROUPS[today.month]


def monday_query(query):
    req = urllib.request.Request(
        "https://api.monday.com/v2",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json", "Authorization": MONDAY_API_TOKEN},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def fetch_lo_sagar():
    """מחזיר {name, phone, agent} של כל מי שהפך ל'לא סגר' החודש."""
    all_logs = []
    page = 1
    while page <= 100:
        q = f"""query {{
          boards(ids: {LEADS_BOARD_ID}) {{
            activity_logs(from: "{month_start}", limit: 100, page: {page}) {{
              event
              data
            }}
          }}
        }}"""
        result = monday_query(q)
        logs = result["data"]["boards"][0]["activity_logs"]
        if not logs:
            break
        all_logs.extend(logs)
        if len(logs) < 100:
            break
        page += 1

    changed_ids = {}
    for log in all_logs:
        if log.get("event") != "update_column_value":
            continue
        try:
            data = json.loads(log.get("data") or "{}")
        except:
            continue
        if data.get("column_id") != LEADS_STATUS_COL:
            continue
        val = data.get("value", {})
        label = val.get("label", {}) if isinstance(val, dict) else {}
        if label.get("index") == LO_SAGAR_INDEX:
            pid = str(data.get("pulse_id", ""))
            if pid and pid not in changed_ids:
                changed_ids[pid] = data.get("pulse_name", "")

    if not changed_ids:
        return []

    results = []
    ids = list(changed_ids.keys())
    for i in range(0, len(ids), 50):
        chunk = ", ".join(ids[i:i+50])
        q = f"""query {{
          items(ids: [{chunk}]) {{
            id
            name
            column_values(ids: ["text__1", "phone__1", "{LEADS_AGENT_COL}"]) {{ id text }}
          }}
        }}"""
        res = monday_query(q)
        for item in (res.get("data") or {}).get("items", []):
            name = item.get("name", changed_ids.get(item["id"], ""))
            phone = agent = ""
            for col in item["column_values"]:
                if col["id"] == "text__1" and col["text"]:
                    name = col["text"]
                elif col["id"] == "phone__1" and col["text"]:
                    phone = col["text"]
                elif col["id"] == LEADS_AGENT_COL and col["text"]:
                    agent = col["text"]
            results.append({"name": name, "phone": phone, "agent": agent})
    return results


def fetch_canceled_under_500():
    """מחזיר {name, phone, amount, agent} של כל מי שביטל בחודש זה עם תשלום < 500₪."""
    all_items = []
    cursor = None
    while True:
        cursor_arg = f', cursor: "{cursor}"' if cursor else ""
        q = f"""query {{
          boards(ids: {TRANSACTIONS_BOARD_ID}) {{
            groups(ids: ["{current_group}"]) {{
              items_page(limit: 500{cursor_arg}) {{
                cursor
                items {{
                  id
                  column_values(ids: ["text__1", "phone__1", "{CANCEL_COL}", "{AMOUNT_COL}", "{CANCEL_AGENT_COL}"]) {{
                    id text
                  }}
                }}
              }}
            }}
          }}
        }}"""
        result = monday_query(q)
        group_data = (result["data"]["boards"][0].get("groups") or [{}])[0]
        page = group_data.get("items_page", {})
        all_items.extend(page.get("items", []))
        cursor = page.get("cursor")
        if not cursor:
            break

    results = []
    for item in all_items:
        vals = {c["id"]: (c["text"] or "").strip() for c in item["column_values"]}
        if vals.get(CANCEL_COL) != CANCEL_LABEL:
            continue
        try:
            amount = float(vals.get(AMOUNT_COL, "0").replace(",", "") or "0")
        except ValueError:
            amount = 0.0
        if amount >= CANCEL_THRESHOLD:
            continue
        results.append({
            "name":   vals.get("text__1", ""),
            "phone":  vals.get("phone__1", ""),
            "amount": amount,
            "agent":  vals.get(CANCEL_AGENT_COL, ""),
        })
    return results


def build_message(lo_sagar, canceled):
    """מחזיר הודעה אחת עם כל הנציגים."""
    agents = sorted(set(
        [p["agent"] for p in lo_sagar if p["agent"]] +
        [p["agent"] for p in canceled if p["agent"]]
    ))

    lines = [f"📋 *סיסטם בשורות טובות — {month_label} {today.year}*"]

    for agent in agents:
        ag_canceled = [p for p in canceled if p["agent"] == agent]
        ag_lo_sagar = [p for p in lo_sagar if p["agent"] == agent]

        lines.append("")
        lines.append(f"👤 *נציג {agent}*")

        lines.append("")
        lines.append("*ביטלו*")
        if ag_canceled:
            for p in ag_canceled:
                lines.append(f"• {p['name']} | {p['phone']}")
        else:
            lines.append("אין")

        lines.append("")
        lines.append("*סטטוס לא סגר*")
        if ag_lo_sagar:
            for p in ag_lo_sagar:
                lines.append(f"• {p['name']} | {p['phone']}")
        else:
            lines.append("אין")

    return "\n".join(lines)


def send_telegram(message):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=json.dumps({"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def main():
    force = os.environ.get("FORCE_RUN", "").lower() == "true"
    if not force and today.day != last_day:
        print(f"היום {today.day}, היום האחרון הוא {last_day}. לא שולח.")
        sys.exit(0)

    print(f"סיסטם בשורות טובות — {month_label} {today.year} (יום אחרון: {last_day})")

    lo_sagar = fetch_lo_sagar()
    print(f"לא סגרו: {len(lo_sagar)}")

    canceled = fetch_canceled_under_500()
    print(f"ביטלו: {len(canceled)}")

    message = build_message(lo_sagar, canceled)
    send_telegram(message)
    print("נשלח ✓")

    print("הכל נשלח!")


if __name__ == "__main__":
    main()
