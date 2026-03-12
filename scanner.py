import os
import sys
import time
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS")
ALERT_EMAIL    = os.environ.get("ALERT_EMAIL")
SCAN_INTERVAL  = int(os.environ.get("SCAN_INTERVAL", "900"))
MIN_SEATS      = int(os.environ.get("MIN_SEATS", "2"))
TM_API_KEY     = os.environ.get("TM_API_KEY")

WATCHLIST = [
    "project hail mary",
    "hail mary",
    "sinners",
    "one battle after another",
    "f1",
    "the odyssey",
    "dune: part three",
    "dune part three",
    "flowervale street",
]

FORMAT_KEYWORDS = ["imax", "70mm", "plf", "prime", "laser", "large format"]
SWEET_ROWS      = {"F","G","H","I","J","K","L"}
SHOWTIME_STATE  = {}

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def save_state():
    try:
        with open("state.json", "w") as f:
            json.dump(SHOWTIME_STATE, f)
    except Exception as e:
        log(f"State save error: {e}")

def load_state():
    global SHOWTIME_STATE
    try:
        with open("state.json") as f:
            SHOWTIME_STATE = json.load(f)
        log(f"Loaded state for {len(SHOWTIME_STATE)} tracked showtimes")
    except:
        log("No previous state — starting fresh")

def get_imax_showtimes():
    results = []
    log(f"  TM_API_KEY present: {bool(TM_API_KEY)}")

    if not TM_API_KEY:
        log("  ERROR: TM_API_KEY not set in Railway Variables!")
        return results

    # Search by keyword for each watchlist film at Lincoln Square NY
    for film in ["project hail mary", "sinners", "f1"]:
        try:
            url    = "https://app.ticketmaster.com/discovery/v2/events.json"
            params = {
                "apikey":   TM_API_KEY,
                "keyword":  film,
                "city":     "New York",
                "stateCode": "NY",
                "size":     20,
            }
            log(f"  Searching TM for: '{film}'")
            resp = requests.get(url, params=params, timeout=15)
            log(f"  HTTP {resp.status_code}")

            if resp.status_code != 200:
                log(f"  TM error body: {resp.text[:300]}")
                continue

            data   = resp.json()
            total  = data.get("page", {}).get("totalElements", 0)
            events = data.get("_embedded", {}).get("events", [])
            log(f"  '{film}': {total} total results, {len(events)} returned")

            for event in events:
                name  = event.get("name", "")
                venue = event.get("_embedded", {}).get("venues", [{}])[0]
                vname = venue.get("name", "")
                log(f"    EVENT: '{name}' at '{vname}'")

                # Must be at Lincoln Square
                if "lincoln square" not in vname.lower():
                    continue

                info     = event.get("info", "").lower()
                note     = event.get("pleaseNote", "").lower()
                combined = name.lower() + " " + info + " " + note
                is_large = any(k in combined for k in FORMAT_KEYWORDS)
                log(f"    → Lincoln Square match! large_format={is_large}")

                results.append({
                    "title":        name,
                    "showtime":     event.get("dates", {}).get("start", {}).get("dateTime", ""),
                    "showtime_id":  event.get("id", ""),
                    "seats_avail":  99,
                    "total_seats":  0,
                    "purchase_url": event.get("url", ""),
                })

        except Exception as e:
            log(f"  TM search error for '{film}': {e}")

    return results

def send_alert(title, showtime_str, purchase_url, is_return):
    try:
        msg            = MIMEMultipart("alternative")
        msg["Subject"] = f"🎬 IMAX 70MM — {title.upper()}"
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = ALERT_EMAIL

        try:
            dt           = datetime.fromisoformat(showtime_str.replace("Z",""))
            show_display = dt.strftime("%A %b %-d · %-I:%M %p")
        except:
            show_display = showtime_str or "See link for times"

        text = f"""
IMAX 70MM SEATS — {title.upper()}
{"─"*42}
{show_display} · AMC Lincoln Square 13
Sweet spot seats available (rows F–L)
→ BOOK: {purchase_url}
{"─"*42}
        """.strip()

        html = f"""
<html><body style="font-family:monospace;background:#0a0a08;color:#e8e0cc;padding:32px;max-width:500px;margin:0 auto;">
  <div style="border-left:3px solid #e8c547;padding-left:20px;margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:.3em;color:rgba(232,197,71,.5);margin-bottom:8px;">AMC LINCOLN SQUARE · IMAX 70MM</div>
    <div style="font-size:26px;font-weight:900;color:#e8c547;">{title.upper()}</div>
    <div style="font-size:13px;color:rgba(255,255,255,.45);margin-top:4px;">{show_display}</div>
  </div>
  <div style="background:rgba(232,197,71,.06);border:1px solid rgba(232,197,71,.18);padding:16px;margin-bottom:20px;">
    <div style="font-size:13px;color:#e8c547;">Sweet spot seats available · Rows F–L · 2 adjacent</div>
  </div>
  <a href="{purchase_url}" style="display:block;background:#e8c547;color:#0a0a08;text-align:center;padding:14px;font-size:11px;font-weight:900;letter-spacing:.3em;text-decoration:none;margin-bottom:20px;">BOOK NOW →</a>
</body></html>"""

        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
            s.sendmail(GMAIL_ADDRESS, ALERT_EMAIL, msg.as_string())
        log(f"✅ Alert sent: {title} — {show_display}")
        return True
    except Exception as e:
        log(f"❌ Email error: {e}")
        return False

def scan():
    log("─── Scanning Ticketmaster for IMAX 70MM at Lincoln Square...")
    showtimes = get_imax_showtimes()
    if not showtimes:
        log("No matching showtimes found.")
        return
    log(f"Found {len(showtimes)} showtime(s) to evaluate.")

    for show in showtimes:
        sid   = show["showtime_id"]
        state = SHOWTIME_STATE.get(sid, {"alerted": False})

        if state["alerted"]:
            log(f"  Skip — already alerted: {show['title']}")
            continue

        sent = send_alert(
            title=show["title"],
            showtime_str=show["showtime"],
            purchase_url=show["purchase_url"],
            is_return=False,
        )
        if sent:
            state["alerted"] = True

        SHOWTIME_STATE[sid] = state
        save_state()

def main():
    log("🎬 IMAX 70MM Seat Watcher started")
    log(f"   Python: {sys.version}")
    log(f"   Watching:  {', '.join(WATCHLIST[:4])}...")
    log(f"   Theater:   AMC Lincoln Square 13")
    log(f"   Interval:  every {SCAN_INTERVAL // 60} min")
    log(f"   Alerting:  {ALERT_EMAIL}")
    log(f"   TM Key:    {'SET ✓' if TM_API_KEY else 'MISSING ✗'}")
    load_state()
    while True:
        try:
            scan()
        except Exception as e:
            log(f"Scan error: {e}")
            import traceback
            traceback.print_exc()
        log(f"Next scan in {SCAN_INTERVAL // 60} min...")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
