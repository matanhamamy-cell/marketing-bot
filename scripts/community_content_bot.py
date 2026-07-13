#!/usr/bin/env python3
"""
בוט "הקהילה של מתן חממי" — שולח למתן תבנית תוכן מוכנה + הנחיית משלוח,
לפי לוח תוכן שבועי לקבוצת הווצאפ השקטה שלו. רץ באמצעות GitHub Actions
בימי ראשון/שני/רביעי/חמישי/שישי בבוקר שעון ישראל.

מתן מעתיק-מדביק את התבנית לקבוצה. פעם ב-7-10 ימים בלבד מגיע סלוט עם
קריאה לפעולה (CTA) ולינק Bitly מתויג למעקב קליקים.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
COMMUNITY_BOT_TOKEN = os.environ["COMMUNITY_BOT_TOKEN"]
COMMUNITY_CHAT_ID = os.environ["COMMUNITY_CHAT_ID"]
BITLY_ACCESS_TOKEN = os.environ.get("BITLY_ACCESS_TOKEN", "")
BIO_BITLINK_TARGET = os.environ.get("BIO_BITLINK", "")  # יעד ה-CTA (דף נחיתה/הרשמה)

FORCE_SLOT = os.environ.get("FORCE_SLOT", "").strip()  # לבדיקה ידנית דרך workflow_dispatch
MIN_DAYS_BETWEEN_CTA = 7

STATE_FILE = "/tmp/last_community_cta.txt"
LINKS_LOG = os.path.join(SCRIPT_DIR, "community_links_log.csv")

ANTHROPIC_MODEL = "claude-sonnet-5"

# יום בשבוע (Python weekday: Monday=0 ... Sunday=6) → סלוט
SLOT_BY_WEEKDAY = {
    6: "value",         # ראשון
    0: "social_proof",  # שני
    2: "lifestyle",     # רביעי
    3: "motivational",  # חמישי
    4: "cta",           # שישי
}

SLOT_LABELS = {
    "value": "ערך/חינוכי",
    "social_proof": "הוכחה חברתית",
    "lifestyle": "תוכן אישי/lifestyle",
    "motivational": "קופי מוטיבציוני",
    "cta": "קריאה לפעולה",
}

SLOT_INSTRUCTIONS = {
    "value": (
        "כתוב פוסט ערך טהור: מיתוס נפוץ או טעות שאנשים עושים בדרופשיפינג/eBay, "
        "ולמה זה לא נכון. בלי לינק, בלי קריאה למכירה. המטרה היא לבנות אמון וסמכות."
    ),
    "social_proof": (
        "כתוב פוסט הוכחה חברתית — הצלחה או תוצאה (יכולה להיות כללית/מייצגת, לא חייבת "
        "להיות מספר אמיתי ספציפי) שממחישה שהשיטה עובדת. בלי להבטיח הכנסה קונקרטית לכל אחד. בלי לינק."
    ),
    "lifestyle": (
        "כתוב רפלקציה אישית קצרה של מתן על החיים/העבודה/החופש שהעסק נותן לו, "
        "ותן הנחיה קצרה איזו תמונה מומלץ לצרף (למשל: תמונה שלו עובד ממקום לא שגרתי, "
        "רגע יומיומי). בלי לינק ברוב המקרים."
    ),
    "motivational": (
        "כתוב קופי מוטיבציוני במבנה: כאב אמיתי של הקהל (למשל תקיעות במשכורת נמוכה, "
        "פחד להתחיל) → הפרכת תירוץ נפוץ → הסיבה האמיתית (חוסר ידע/מיומנות, לא מזל) → "
        "מסר אחריות אישית או שאלה רפלקטיבית לסיום. בלי קריאה למכירה."
    ),
    "cta": (
        "כתוב קריאה לפעולה רכה, לא לוחצת, המזמינה להירשם למרתון/הדרכה חינמית או לקבל "
        "פרטים נוספים. תשלב את הלינק הבא במקום טבעי בהודעה: {link}"
    ),
}


def load_text(filename: str) -> str:
    path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def determine_slot() -> str:
    if FORCE_SLOT:
        return FORCE_SLOT
    weekday = datetime.now().weekday()
    return SLOT_BY_WEEKDAY.get(weekday, "value")


def days_since_last_cta() -> int | None:
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        last = f.read().strip()
    if not last:
        return None
    last_date = date.fromisoformat(last)
    return (date.today() - last_date).days


def record_cta_sent() -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(date.today().isoformat())


def create_bitly_link(campaign_tag: str) -> str:
    if not BITLY_ACCESS_TOKEN or not BIO_BITLINK_TARGET:
        return BIO_BITLINK_TARGET or ""
    payload = json.dumps({
        "long_url": BIO_BITLINK_TARGET,
        "tags": [f"community-{campaign_tag}"],
    }).encode()
    req = urllib.request.Request(
        "https://api-ssl.bitly.com/v4/bitlinks",
        data=payload,
        headers={
            "Authorization": f"Bearer {BITLY_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
        short_link = result.get("link", "")
        link_id = result.get("id", "")
        log_link(campaign_tag, short_link, link_id)
        return short_link
    except urllib.error.URLError as e:
        print(f"[WARN] Bitly link creation failed: {e}")
        return BIO_BITLINK_TARGET


def log_link(campaign_tag: str, short_link: str, link_id: str) -> None:
    is_new = not os.path.exists(LINKS_LOG)
    with open(LINKS_LOG, "a", encoding="utf-8") as f:
        if is_new:
            f.write("date,campaign_tag,short_link,link_id\n")
        f.write(f"{date.today().isoformat()},{campaign_tag},{short_link},{link_id}\n")


def build_system_prompt(slot: str, link: str | None) -> str:
    context = load_text("community_bot_context.md")
    voice_samples = load_text("community_voice_samples.md")
    instruction = SLOT_INSTRUCTIONS[slot]
    if link:
        instruction = instruction.format(link=link)

    return f"""אתה עוזר תוכן שכותב עבור מתן חממי, שמנהל קהילת ווטסאפ שקטה (broadcast) \
לחימום לידים לעסק הדרופשיפינג/הליווי שלו.

{context}

---

{voice_samples}

---

## המשימה שלך היום

סלוט היום: {SLOT_LABELS[slot]}
{instruction}

## פורמט תשובה (חובה להקפיד בדיוק)

תענה בשני חלקים מסומנים בדיוק כך, בלי טקסט נוסף לפני/אחרי:

### תבנית להעתקה
<כאן הטקסט המוכן להעתקה-הדבקה לקבוצת הווטסאפ, בעברית, בשורות שבורות קצרות \
כמו שמתואר במסמך ההקשר, בלי מילים מנופחות ובלי סימוני markdown>

### הנחיית משלוח
<כאן הנחיה קצרה אחת-שתיים שורות למתן: לשלוח כטקסט רגיל / להקליט כהודעה קולית / \
לצרף תמונה מסוג מסוים / לצרף סרטון קצר — ולמה זה מתאים לסלוט הזה>
"""


def call_claude(system_prompt: str) -> str:
    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1200,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": "צור עכשיו את התוכן להיום לפי ההנחיות."}
        ],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        result = json.loads(r.read())
    return "".join(block["text"] for block in result["content"] if block["type"] == "text")


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


def format_message(slot: str, claude_output: str) -> str:
    today_label = date.today().strftime("%d/%m")
    return (
        f"📅 <b>{today_label} — סלוט: {SLOT_LABELS[slot]}</b>\n\n"
        f"{claude_output}\n\n"
        f"יאללה, קדימה 💪"
    )


def main() -> None:
    slot = determine_slot()

    if slot == "cta":
        since = days_since_last_cta()
        if since is not None and since < MIN_DAYS_BETWEEN_CTA:
            print(f"[INFO] רק {since} ימים מאז ה-CTA האחרון — עובר לסלוט 'value' במקום.")
            slot = "value"

    link = None
    if slot == "cta":
        link = create_bitly_link(date.today().isoformat())

    system_prompt = build_system_prompt(slot, link)
    claude_output = call_claude(system_prompt)
    message = format_message(slot, claude_output)

    print("\n--- MESSAGE PREVIEW ---")
    print(message)
    print("---\n")

    send_telegram(message)

    if slot == "cta":
        record_cta_sent()

    print("[INFO] Done ✅")


if __name__ == "__main__":
    main()
