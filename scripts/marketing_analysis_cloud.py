#!/usr/bin/env python3
"""
ניתוח שיווק שבועי — רץ ב-GitHub Actions כל ראשון ורביעי
מושך נתונים ישירות מ-Meta API + Monday.com ושולח לטלגרם
"""

import os, json, urllib.request, urllib.parse, urllib.error
from datetime import date, timedelta

# ── הגדרות מ-Environment Variables ──
META_ACCESS_TOKEN  = os.environ["META_ACCESS_TOKEN"]
META_AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]
MONDAY_API_TOKEN   = os.environ["MONDAY_API_TOKEN"]
MONDAY_BOARD_ID    = os.environ["MONDAY_BOARD_ID"]
MONDAY_AD_COL      = os.environ.get("MONDAY_AD_SERIES_COL", "text99__1")
MONDAY_AGE_COL     = os.environ.get("MONDAY_AGE_COL",       "numbers_Mjj4CYYs")
MONDAY_SOURCE_COL  = os.environ.get("MONDAY_SOURCE_COL",    "status1__1")
MONDAY_DATE_COL    = os.environ.get("MONDAY_DATE_COL",       "date__1")
TG_TOKEN           = os.environ["TG_TOKEN"]
TG_CHAT_ID         = os.environ["TG_CHAT_ID"]

# ── תאריכים: תחילת החודש עד היום ──
today      = date.today()
start_date = today.replace(day=1).isoformat()
end_date   = today.isoformat()

print(f"[INFO] Period: {start_date} → {end_date}")

# ─────────────────────────────────────────
# Meta API
# ─────────────────────────────────────────
def meta_get(path, params):
    params["access_token"] = META_ACCESS_TOKEN
    url = f"https://graph.facebook.com/v19.0/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())

def fetch_meta_insights():
    rows, url = [], None
    params = {
        "fields": "adset_id,adset_name,campaign_name,spend,clicks,cpc",
        "level": "adset",
        "time_range": json.dumps({"since": start_date, "until": end_date}),
        "limit": "500",
    }
    data = meta_get(f"{META_AD_ACCOUNT_ID}/insights", params)
    rows.extend(data.get("data", []))
    while data.get("paging", {}).get("next"):
        with urllib.request.urlopen(data["paging"]["next"], timeout=30) as r:
            data = json.loads(r.read())
        rows.extend(data.get("data", []))
    return [r for r in rows if "מתנה חינמית" in r.get("campaign_name", "")]

def fetch_meta_statuses():
    data = meta_get(f"{META_AD_ACCOUNT_ID}/adsets",
                    {"fields": "id,effective_status", "limit": "500"})
    return {a["id"]: a["effective_status"] for a in data.get("data", [])}

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

def fetch_monday_leads():
    leads, cursor = [], None
    col_ids = [MONDAY_AD_COL, MONDAY_AGE_COL, MONDAY_SOURCE_COL, MONDAY_DATE_COL, "text0__1", "project_status"]
    col_arg = ", ".join(f'"{c}"' for c in col_ids)

    while True:
        cursor_arg = f', cursor: "{cursor}"' if cursor else ""
        q = f"""query {{
          boards(ids: {MONDAY_BOARD_ID}) {{
            items_page(limit: 500{cursor_arg}) {{
              cursor
              items {{
                id name created_at
                column_values(ids: [{col_arg}]) {{ id text }}
              }}
            }}
          }}
        }}"""
        data = monday_query(q)
        page = data["data"]["boards"][0]["items_page"]
        items = page["items"]
        if not items:
            break

        for item in items:
            vals = {c["id"]: (c["text"] or "") for c in item["column_values"]}
            ad_series  = vals.get(MONDAY_AD_COL, "").strip()
            campaign   = vals.get("text0__1", "").strip()
            source     = vals.get(MONDAY_SOURCE_COL, "")
            status     = vals.get("project_status", "")
            date_val   = (vals.get(MONDAY_DATE_COL, "") or "").split(" ")[0]
            age        = int(vals.get(MONDAY_AGE_COL, "0") or 0)

            if not date_val and item.get("created_at"):
                date_val = item["created_at"][:10]

            if ad_series and "גיוסים" not in campaign:
                leads.append({"adSeriesName": ad_series, "source": source,
                               "status": status, "date": date_val, "age": age})

        cursor = page.get("cursor")
        if not cursor:
            break

    return leads

# ─────────────────────────────────────────
# חישוב מדדים
# ─────────────────────────────────────────
def normalize(s):
    import re
    return re.sub(r"\s+", " ", re.sub(r"[‏‎‪-‮⁦-⁩]", "", s)).strip().lower()

def compute_metrics(insights, statuses, leads):
    filtered = [l for l in leads if start_date <= l["date"] <= end_date]
    leads_by_series = {}
    for l in filtered:
        k = normalize(l["adSeriesName"])
        leads_by_series.setdefault(k, []).append(l)

    metrics = []
    for row in insights:
        key   = normalize(row["adset_name"].strip())
        spent = float(row.get("spend", 0))
        if spent == 0:
            continue
        ad_leads    = leads_by_series.get(key, [])
        gifts       = sum(1 for l in ad_leads if l["source"] == "מתנה חינמית")
        consult     = sum(1 for l in ad_leads if l["source"] == "מתנה חינמית - שיחת ייעוץ")
        transactions= sum(1 for l in ad_leads if l["status"] == "סגר")
        total       = len(ad_leads)
        metrics.append({
            "adSetName":          row["adset_name"].strip(),
            "budget":             spent,
            "totalLeads":         total,
            "gifts":              gifts,
            "directLeads":        consult,
            "transactions":       transactions,
            "costPerLead":        spent/total    if total > 0    else 0,
            "costPerLeadGift":    spent/gifts    if gifts > 0    else 0,
            "costPerLeadDirect":  spent/consult  if consult > 0  else 0,
            "costPerTransaction": spent/transactions if transactions > 0 else 0,
            "clicks":             int(row.get("clicks", 0)),
            "costPerClick":       float(row.get("cpc", 0) or 0),
            "childrenCount":      sum(1 for l in ad_leads if l["age"] < 18),
            "childrenPercentage": (sum(1 for l in ad_leads if l["age"] < 18)/total*100) if total > 0 else 0,
            "active":             statuses.get(row["adset_id"]) == "ACTIVE",
            "pageConversionRate": 0, "pageConversionRateGift": 0, "notes": "",
            "date": end_date, "dateRange": f"{start_date} to {end_date}",
        })
    return metrics

# ─────────────────────────────────────────
# ניתוח
# ─────────────────────────────────────────
def analyze(metrics):
    total_budget = sum(m["budget"]       for m in metrics)
    total_leads  = sum(m["totalLeads"]   for m in metrics)
    total_gifts  = sum(m["gifts"]        for m in metrics)
    total_tx     = sum(m["transactions"] for m in metrics)
    avg_cpg      = total_budget / total_gifts if total_gifts > 0 else 0

    stop, reactivate, excellent, decent, poor = [], [], [], [], []

    for m in sorted(metrics, key=lambda x: -x["budget"]):
        if m["budget"] < 50:
            continue
        cpg = m["budget"] / m["gifts"] if m["gifts"] > 0 else float("inf")
        if not m["active"] and cpg <= 22 and m["totalLeads"] >= 10:
            reactivate.append(m)
        elif m["active"] and ((cpg > 35 and m["totalLeads"] < 15) or cpg > 50):
            stop.append(m)
        elif cpg <= 21:
            excellent.append(m)
        elif cpg <= 28:
            decent.append(m)
        else:
            poor.append(m)

    def fmt(n):  return f"₪{round(n)}"
    def fmtd(n): return f"₪{n:.1f}"

    lines = []
    lines.append(f"📊 *ניתוח שיווק שבועי*")
    lines.append(f"_{start_date} עד {end_date}_")
    lines.append("")

    # סיכום
    active_count = sum(1 for m in metrics if m["active"])
    tx_rate = f"{(total_tx/total_gifts*100):.1f}%" if total_gifts > 0 else "0%"
    summary = f"רצו {active_count} סדרות פעילות עם הוצאה של {fmt(total_budget)}. "
    summary += f"{total_leads} לידים, {total_gifts} מתנות, {total_tx} עסקאות ({tx_rate} המרה). "
    if stop:
        summary += f"⚠️ {len(stop)} סדרות פעילות לעצירה מיידית."
    elif excellent:
        summary += f"✅ {len([m for m in excellent if m['active']])} סדרות בביצועים מעולים."
    lines.append(summary)
    lines.append("")

    # ביצועים
    lines.append("*ביצועים לפי סדרה:*")
    for m in stop:
        cpg = fmtd(m["budget"]/m["gifts"]) if m["gifts"] > 0 else "—"
        lines.append(f"❌ {m['adSetName']} — {cpg}/מתנה | {m['totalLeads']} לידים | עצור מיידי")
    poor_inactive = [m for m in poor if not m["active"]]
    if poor_inactive:
        lines.append(f"❌ כבויות שלא כדאי להחזיר: {' · '.join(m['adSetName'] for m in poor_inactive)}")
    for m in decent:
        cpg = fmtd(m["budget"]/m["gifts"]) if m["gifts"] > 0 else "—"
        cpt = fmt(m["budget"]/m["transactions"]) if m["transactions"] > 0 else "—"
        lines.append(f"⚠️ {m['adSetName']} — {cpg}/מתנה | {m['transactions']} עסקאות ({cpt}/עסקה)")
    for m in reactivate:
        cpg = fmtd(m["budget"]/m["gifts"]) if m["gifts"] > 0 else "—"
        lines.append(f"🔄 {m['adSetName']} (כבוי) — {cpg}/מתנה | {m['totalLeads']} לידים | שקול הפעלה")
    for m in excellent:
        cpg = fmtd(m["budget"]/m["gifts"]) if m["gifts"] > 0 else "—"
        cpt = fmt(m["budget"]/m["transactions"]) if m["transactions"] > 0 else ""
        tx_note = f" | {m['transactions']} עסקאות ({cpt}/עסקה)" if m["transactions"] > 0 else ""
        lines.append(f"✅ {m['adSetName']} ({'פעיל' if m['active'] else 'כבוי'}) — {cpg}/מתנה | {m['totalLeads']} לידים{tx_note}")
    lines.append("")

    # פעולות
    lines.append("*⚡ פעולות מיידיות:*")
    if stop:
        lines.append(f"❌ עצור עכשיו: {', '.join(m['adSetName'] for m in stop)}")
    if reactivate:
        lines.append(f"🔄 הפעל מחדש: {', '.join(m['adSetName'] for m in reactivate)}")
    top_active = [m for m in excellent if m["active"]][:2]
    if top_active:
        lines.append(f"✅ הגדל תקציב: {', '.join(m['adSetName'] for m in top_active)}")
    big_no_tx = next((m for m in sorted(metrics, key=lambda x: -x["budget"])
                      if m["active"] and m["transactions"] == 0 and m["budget"] > 800), None)
    if big_no_tx:
        cpg = fmtd(big_no_tx["budget"]/big_no_tx["gifts"]) if big_no_tx["gifts"] > 0 else "—"
        lines.append(f"⚠️ בחן: {big_no_tx['adSetName']} — {fmt(big_no_tx['budget'])} הוצאה, {cpg}/מתנה, 0 עסקאות")

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
    print("[INFO] Fetching Meta insights...")
    insights = fetch_meta_insights()
    print(f"[INFO] Got {len(insights)} ad sets from Meta")

    print("[INFO] Fetching Meta statuses...")
    statuses = fetch_meta_statuses()

    print("[INFO] Fetching Monday leads...")
    leads = fetch_monday_leads()
    print(f"[INFO] Got {len(leads)} leads from Monday")

    print("[INFO] Computing metrics...")
    metrics = compute_metrics(insights, statuses, leads)
    print(f"[INFO] {len(metrics)} ad sets with spend")

    print("[INFO] Analyzing...")
    message = analyze(metrics)

    print("[INFO] Sending to Telegram...")
    send_telegram(message)
    print("[INFO] Done ✅")
