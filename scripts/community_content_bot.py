#!/usr/bin/env python3
"""
בוט "הקהילה של מתן חממי" — שולח למתן תבנית תוכן מוכנה + הנחיית משלוח,
לפי לוח תוכן שבועי לקבוצת הווצאפ השקטה שלו. רץ באמצעות GitHub Actions
בימי ראשון/רביעי/שישי בבוקר שעון ישראל (3 פעמים בשבוע - קצב מתון בכוונה).

ראשון = ערך קבוע. רביעי מתחלף בין הוכחה חברתית ותוכן אישי (לפי זוגיות
מספר השבוע). שישי הוא בדרך כלל קופי מוטיבציוני, ומתחלף ל-CTA עם לינק
Bitly מתויג פעם ב-14 יום בלבד (כל שישי שני). מתן מעתיק-מדביק את התבנית
לקבוצה.
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

FORCE_SLOT = os.environ.get("FORCE_SLOT", "").strip()  # לבדיקה ידנית / הפעלת סלוט חד-פעמי דרך workflow_dispatch
MIN_DAYS_BETWEEN_CTA = 14  # שישי אחד מתוך שניים בממוצע (בקשת מתן: 2-3 הודעות בשבוע בסה"כ, CTA נדיר בתוכן)

STATE_FILE = "/tmp/last_community_cta.txt"
LINKS_LOG = os.path.join(SCRIPT_DIR, "community_links_log.csv")

ANTHROPIC_MODEL = "claude-sonnet-5"

SLOT_LABELS = {
    "value": "ערך/חינוכי",
    "social_proof": "הוכחה חברתית",
    "lifestyle": "תוכן אישי/lifestyle",
    "motivational": "קופי מוטיבציוני",
    "cta": "קריאה לפעולה",
    "launch": "הודעת פתיחה לקהילה (חד-פעמי)",
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
    "launch": (
        "זו ההודעה הראשונה שאי פעם תישלח לקהילה - הרגע שאנשים מצטרפים דרך לינק שמתן "
        "שלח לכל רשימת הלידים שלו. זו לא הודעת ערך רגילה - זו הודעת פתיחה סוחפת ברמת "
        "פיתוח אישי, שמטרתה לגרום לכל מי שמצטרף להרגיש שהוא בדיוק במקום הנכון ברגע הנכון. "
        "מבנה מוצע: פתיחה דרמטית שמדברת ישר לרגע הזה בחיים של הקורא (לא לתחום הדרופשיפינג "
        "עצמו) - תחושת תקיעות, חיפוש משהו יותר, ההחלטה להצטרף לקבוצה הזו כרגע קטן אבל "
        "משמעותי → הבטחה כנה (לא מוגזמת) על מה הקהילה הזו תיתן - לא הבטחת כסף, אלא כיוון, "
        "כלים, ליווי, ולא להיות לבד בדרך → סגירה בטון אישי וחם, בלי למכור כלום ובלי לינק - "
        "רק הזמנה להישאר ולראות. חובה להשתמש בסגנון של מתן (משפטים שבורים, מטאפורה חתימה, "
        "כנות לא מנוקה) אבל ברמת אנרגיה והתרוממות גבוהה יותר מכל סלוט אחר - זו ה'בוננזה'."
    ),
}


def load_text(filename: str) -> str:
    path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def determine_slot() -> str:
    """
    ראשון (weekday=6) → value קבוע.
    רביעי (weekday=2) → מתחלף לפי זוגיות מספר השבוע (ISO): social_proof / lifestyle.
    שישי (weekday=4) → motivational כברירת מחדל, cta אם עברו מספיק ימים מה-CTA הקודם.
    """
    if FORCE_SLOT:
        return FORCE_SLOT

    today = date.today()
    weekday = today.weekday()

    if weekday == 6:
        return "value"
    if weekday == 2:
        week_number = today.isocalendar()[1]
        return "social_proof" if week_number % 2 == 0 else "lifestyle"
    if weekday == 4:
        since = days_since_last_cta()
        if since is None or since >= MIN_DAYS_BETWEEN_CTA:
            return "cta"
        return "motivational"

    return "value"  # לא אמור לקרות - ה-cron רץ רק בימים שלמעלה


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
