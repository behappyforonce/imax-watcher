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

# AMC Lincoln Square 13 — theatre ID 1076
THEATRE_ID = "1076"

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

def try_amc_mobile_api():
    """AMC's mobile app API — different endpoint, different headers, less bot protection."""
    results = []
    today   = datetime.now().strftime("%Y-%m-%d")

    # Try multiple AMC API endpoints used by their iOS/Android app
    endpoints = [
        f"https://api.amctheatres.com/v2/theatres/{THEATRE_ID}/showtimes/{today}",
        f"https://api.amctheatres.com/v2/theatres/{THEATRE_ID}/showtimes",
        f"https://www.amctheatres.com/api/v2/theatres/{THEATRE_ID}/showtimes/{today}",
    ]

    headers_variants = [
        # Mobile app headers
        {
            "User-Agent": "AMC/5.x (iPhone; iOS 17.0; Scale/3.0)",
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "X-AMC-Vendor-Key": "amc",
        },
        # Android app headers
        {
            "User-Agent": "AMC Theatres/5.x (Android 14; Build/UQ1A)",
            "Accept": "application/json",
            "Accept-Language": "en-US",
        },
        # Generic JSON headers
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.amctheatres.com/",
        },
    ]

    for url in endpoints:
        for headers in headers_variants:
            try:
                log(f"  Trying: {url}")
                resp = requests.get(url, headers=headers, timeout=15)
                log(f"  HTTP {resp.status_code} — {len(resp.content)} bytes — Content-Type: {resp.headers.get('Content-Type','?')[:40]}")

                if resp.status_code == 200 and "json" in resp.headers.get("Content-Type", ""):
                    data = resp.json()
                    log(f"  JSON keys: {list(data.keys())[:10]}")

                    # Extract showtimes from whatever structure this returns
                    showtimes = (
                        data.get("showtimes") or
                        data.get("data") or
                        data.get("_embedded", {}).get("showtimes", []) or
                        []
                    )
                    log(f"  Found {len(showtimes)} showtimes")

                    for st in showtimes:
                        title     = st.get("movieName") or st.get("title") or st.get("name") or ""
                        title_low = title.lower()
                        fmt       = str(st.get("attributes") or st.get("format") or st.get("attributeIds") or "").lower()
                        desc      = str(st.get("description") or "").lower()
                        combined  = title_low + " " + fmt + " " + desc

                        is_watchlist = any(w in title_low for w in WATCHLIST)
                        is_large     = any(k in combined for k in FORMAT_KEYWORDS)
                        log(f"    '{title}' fmt='{fmt[:40]}' watchlist={is_watchlist} large={is_large}")

                        if is_watchlist and is_large:
                            sid = str(st.get("id") or st.get("showtimeId") or "")
                            results.append({
                                "title":        title,
                                "showtime":     st.get("showDateTimeLocal") or st.get("startTime") or "",
                                "showtime_id":  sid,
                                "seats_avail":  st.get("seatsAvailable") or st.get("availableSeats") or 99,
                                "total_seats":  st.get("totalSeats") or 0,
                                "purchase_url": f"https://www.amctheatres.com{st.get('purchaseUrl','') or st.get('url','')}",
                            })

                    if results:
                        return results
                    if showtimes:
                        # Got data but no matches — no point trying other header variants
                        break

                elif resp.status_code == 200:
                    snippet = resp.text[:200]
                    log(f"  Non-JSON response snippet: {snippet}")

            except Exception as e:
                log(f"  Request error: {e}")

    return results

def try_fandango():
    """Fandango has AMC Lincoln Square and a more accessible API."""
    results = []
    try:
        # Fandango theater page for AMC Lincoln Square
        url     = "https://www.fandango.com/amc-lincoln-square-13_aaanf/theater-page"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }
        log(f"  Trying Fandango: {url}")
        resp = requests.get(url, headers=headers, timeout=15)
        log(f"  Fandango HTTP {resp.status_code} — {len(resp.content)} bytes")

        if resp.status_code == 200:
            from bs4 import BeautifulSoup
            soup  = BeautifulSoup(resp.text, "html.parser")
            
            # Log all movie titles found
            for el in soup.find_all(attrs={"data-movie-title": True}):
                log(f"  Fandango movie: '{el['data-movie-title']}'")

            movies = soup.find_all(class_=lambda c: c and "fdng-movie" in str(c).lower())
            log(f"  Fandango movie sections: {len(movies)}")

            for movie in movies:
                title_el = movie.find(attrs={"data-movie-title": True}) or movie.find(["h2","h3","h4"])
                if not title_el: continue
                title     = title_el.get("data-movie-title") or title_el.get_text(strip=True)
                title_low = title.lower()
                log(f"  Fandango section: '{title}'")

                if not any(w in title_low for w in WATCHLIST): continue

                for st in movie.find_all(class_=lambda c: c and "showtime" in str(c).lower()):
                    full = st.get_text(separator=" ").lower()
                    if not any(k in full for k in FORMAT_KEYWORDS): continue
                    link = st.find("a", href=True)
                    results.append({
                        "title":        title,
                        "showtime":     "",
                        "showtime_id":  link["href"] if link else title,
                        "seats_avail":  99,
                        "total_seats":  0,
                        "purchase_url": f"https://www.fandango.com{link['href']}" if link else "https://www.fandango.com/amc-lincoln-square-13_aaanf/theater-page",
                    })
                    log(f"  ✓ Fandango match: '{title}'")

    except Exception as e:
        log(f"  Fandango error: {e}")
    return results

def get_imax_showtimes():
    log("  --- Trying AMC mobile API ---")
    results = try_amc_mobile_api()
    if results:
        return results

    log("  --- Trying Fandango ---")
    results = try_fandango()
    return results

def send_alert(title, showtime_str, purchase_url):
    try:
        msg            = MIMEMultipart("alternative")
        msg["Subject"] = f"🎬 IMAX 70MM — {title.upper()}"
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = ALERT_EMAIL

        try:
            dt           = datetime.fromisoformat(showtime_str.replace("Z",""))
            show_display = dt.strftime("%A %b %-d · %-I:%M %p")
        except:
            show_display = showtime_str or "See link for showtime"

        text = f"IMAX 70MM — {title.upper()}\n{show_display} · AMC Lincoln Square 13\nBOOK: {purchase_url}"

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
  <a href="{purchase_url}" style="display:block;background:#e8c547;color:#0a0a08;text-align:center;padding:14px;font-size:11px;font-weight:900;letter-spacing:.3em;text-decoration:none;">BOOK NOW →</a>
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
    log("─── Scanning for IMAX 70MM at Lincoln Square...")
    showtimes = get_imax_showtimes()
    if not showtimes:
        log("No matching showtimes found.")
        return
    log(f"Found {len(showtimes)} showtime(s) to alert on.")
    for show in showtimes:
        sid   = show["showtime_id"]
        state = SHOWTIME_STATE.get(sid, {"alerted": False})
        if state["alerted"]:
            log(f"  Skip — already alerted: {show['title']}")
            continue
        sent = send_alert(show["title"], show["showtime"], show["purchase_url"])
        if sent:
            state["alerted"] = True
        SHOWTIME_STATE[sid] = state
        save_state()

def main():
    log("🎬 IMAX 70MM Seat Watcher started")
    log(f"   Python: {sys.version.split()[0]}")
    log(f"   Watching:  {', '.join(WATCHLIST[:4])}...")
    log(f"   Interval:  every {SCAN_INTERVAL // 60} min")
    log(f"   Alerting:  {ALERT_EMAIL}")
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
