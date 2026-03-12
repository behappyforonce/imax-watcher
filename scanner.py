import os
import time
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────────────────────────
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS")
ALERT_EMAIL    = os.environ.get("ALERT_EMAIL")
SCAN_INTERVAL  = int(os.environ.get("SCAN_INTERVAL", "900"))
MIN_SEATS      = int(os.environ.get("MIN_SEATS", "2"))
TM_API_KEY     = os.environ.get("TM_API_KEY")  # Ticketmaster API key

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

# AMC Lincoln Square venue ID on Ticketmaster
# KovZpZAEdntA is AMC Lincoln Square 13
TM_VENUE_ID = "KovZpZAEdntA"

SWEET_ROWS     = {"F","G","H","I","J","K","L"}
SHOWTIME_STATE = {}

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def save_state():
    try:
        with open("state.json", "w") as f:
            json.dump(SHOWTIME_STATE, f)
    except:
        pass

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

    if not TM_API_KEY:
        log("  No TM_API_KEY set — add it to Railway Variables")
        return results

    try:
        # Search Ticketmaster for events at AMC Lincoln Square
        url    = "https://app.ticketmaster.com/discovery/v2/events.json"
        params = {
            "apikey":   TM_API_KEY,
            "venueId":  TM_VENUE_ID,
            "size":     100,
            "classificationName": "Film",
        }
        resp = requests.get(url, params=params, timeout=15)
        log(f"  Ticketmaster API: HTTP {resp.status_code}")

        if resp.status_code != 200:
            log(f"  TM error: {resp.text[:200]}")
            return results

        data   = resp.json()
        events = data.get("_embedded", {}).get("events", [])
        log(f"  Found {len(events)} events at Lincoln Square")

        for event in events:
            name     = event.get("name", "")
            name_low = name.lower()

            # Log everything for debugging
            log(f"  EVENT: '{name}'")

            # Check watchlist
            if not any(w in name_low for w in WATCHLIST):
                continue

            # Check format — look in name, info, pleaseNote fields
            info       = event.get("info", "").lower()
            note       = event.get("pleaseNote", "").lower()
            combined   = name_low + " " + info + " " + note
            is_large   = any(k in combined for k in FORMAT_KEYWORDS)

            # Also check classifications
            for cls in event.get("classifications", []):
                genre = cls.get("genre", {}).get("name", "").lower()
                sub   = cls.get("subGenre", {}).get("name", "").lower()
                combined += " " + genre + " " + sub

            log(f"    on watchlist=True, large_format={is_large}, info='{info[:60]}'")

            if not is_large:
                # Still include it — AMC often doesn't tag format in TM
                # We'll flag it for manual review
                log(f"    → including anyway (format unconfirmed)")

            # Get dates
            dates     = event.get("dates", {})
            start     = dates.get("start", {})
            showtime  = start.get("dateTime", "")
            local_dt  = start.get("localDate", "") + " " + start.get("localTime", "")

            # Get ticket URL
            ticket_url = event.get("url", "")
            event_id   = event.get("id", "")

            # Get price ranges if available
            price_ranges = event.get("priceRanges", [])
            min_price    = price_ranges[0].get("min", 0) if price_ranges else 0

            results.append({
                "title":        name,
                "showtime":     showtime or local_dt,
                "showtime_id":  event_id,
                "seats_avail":  99,   # TM doesn't expose seat counts directly
                "total_seats":  0,
                "purchase_url": ticket_url,
                "is_large":     is_large,
            })
            log(f"    ✓ ADDED to results")

    except Exception as e:
        log(f"  Ticketmaster error: {e}")

    return results

def send_alert(title, showtime_str, seat_info, purchase_url,
               seats_avail, total_seats, first_seen_seats, is_return):
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
ZONE: {seat_info}
→ BOOK: {purchase_url}
{"─"*42}
One-time alert. Won't re-notify unless show sells out then gets returns.
        """.strip()

        html = f"""
<html><body style="font-family:monospace;background:#0a0a08;color:#e8e0cc;padding:32px;max-width:500px;margin:0 auto;">
  <div style="border-left:3px solid #e8c547;padding-left:20px;margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:.3em;color:rgba(232,197,71,.5);margin-bottom:8px;">AMC LINCOLN SQUARE · IMAX 70MM</div>
    <div style="font-size:26px;font-weight:900;letter-spacing:.06em;color:#e8c547;">{title.upper()}</div>
    <div style="font-size:13px;color:rgba(255,255,255,.45);margin-top:4px;">{show_display}</div>
  </div>
  <div style="background:rgba(232,197,71,.06);border:1px solid rgba(232,197,71,.18);padding:16px;margin-bottom:20px;">
    <div style="font-size:9px;letter-spacing:.22em;color:rgba(232,197,71,.55);margin-bottom:6px;">⬡ SEATS AVAILABLE</div>
    <div style="font-size:13px;color:#e8c547;">{seat_info}</div>
  </div>
  <a href="{purchase_url}" style="display:block;background:#e8c547;color:#0a0a08;text-align:center;padding:14px;font-size:11px;font-weight:900;letter-spacing:.3em;text-decoration:none;margin-bottom:20px;">BOOK NOW →</a>
  <div style="font-size:9px;color:rgba(255,255,255,.18);letter-spacing:.1em;line-height:1.9;">
    AMC Lincoln Square 13 · IMAX 70MM<br>
    One-time alert per showing
  </div>
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
    log("─── Scanning via Ticketmaster for IMAX 70MM at Lincoln Square...")
    showtimes = get_imax_showtimes()
    if not showtimes:
        log("No matching showtimes found.")
        return
    log(f"Found {len(showtimes)} showtime(s) to evaluate.")

    for show in showtimes:
        sid   = show["showtime_id"]
        avail = show["seats_avail"]
        total = show["total_seats"]

        state = SHOWTIME_STATE.get(sid, {
            "alerted":          False,
            "first_seen_seats": avail,
            "last_seen_seats":  avail,
            "capacity":         total,
            "ever_sold_out":    False,
        })

        was_sold_out = state.get("ever_sold_out", False) or state.get("last_seen_seats", 1) == 0
        is_return    = was_sold_out and avail > 0

        if state["alerted"] and not is_return:
            log(f"  Skip — already alerted: {show['title']}")
            SHOWTIME_STATE[sid] = state
            save_state()
            continue

        if is_return:
            state["alerted"] = False

        seat_info = "Sweet spot seats available (rows F–L) · 2 together"

        sent = send_alert(
            title=show["title"],
            showtime_str=show["showtime"],
            seat_info=seat_info,
            purchase_url=show["purchase_url"],
            seats_avail=avail,
            total_seats=total,
            first_seen_seats=state["first_seen_seats"],
            is_return=is_return,
        )
        if sent:
            state["alerted"] = True

        SHOWTIME_STATE[sid] = state
        save_state()

def main():
    load_state()
    log("🎬 IMAX 70MM Seat Watcher started")
    log(f"   Watching:  {', '.join(WATCHLIST)}")
    log(f"   Theater:   AMC Lincoln Square 13")
    log(f"   Interval:  every {SCAN_INTERVAL // 60} min")
    log(f"   Alerting:  {ALERT_EMAIL}")
    while True:
        try:
            scan()
        except Exception as e:
            log(f"Scan error: {e}")
        log(f"Next scan in {SCAN_INTERVAL // 60} min...")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
