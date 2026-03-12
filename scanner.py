import os
import sys
import time
import json
import smtplib
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
STATE_FILE      = os.path.expanduser("~/.imax_state.json")

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(SHOWTIME_STATE, f)
    except Exception as e:
        log(f"State save error: {e}")

def load_state():
    global SHOWTIME_STATE
    try:
        with open(STATE_FILE) as f:
            SHOWTIME_STATE = json.load(f)
        log(f"Loaded state for {len(SHOWTIME_STATE)} tracked showtimes")
    except:
        log("No previous state — starting fresh")

def get_imax_showtimes():
    results = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("ERROR: Playwright not installed. Run: pip3 install playwright && python3 -m playwright install chromium")
        return results

    try:
        with sync_playwright() as p:
            log("  Launching browser...")
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = context.new_page()

            # Intercept the API response as the page loads
            api_data = {}

            def handle_response(response):
                if "showtimes" in response.url and "amctheatres" in response.url:
                    try:
                        data = response.json()
                        if "_embedded" in data or "showtimes" in data:
                            api_data["showtimes"] = data
                            log(f"  Intercepted API: {response.url[:80]}")
                    except:
                        pass

            page.on("response", handle_response)

            # Navigate to Lincoln Square showtimes page
            url = "https://www.amctheatres.com/theatres/new-york/amc-lincoln-square-13/showtimes/all-movies/today/all-screenings"
            log(f"  Navigating to AMC Lincoln Square...")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            log("  Page loaded — waiting for showtime content...")
            # Wait for showtime content to render (JS-driven)
            try:
                page.wait_for_selector("text=IMAX", timeout=15000)
                log("  Showtime content detected")
            except:
                # If no IMAX text found, just wait a fixed amount
                log("  No IMAX selector found, waiting 10s...")
                page.wait_for_timeout(10000)

            # If we intercepted API data, use it
            if api_data.get("showtimes"):
                data      = api_data["showtimes"]
                showtimes = data.get("_embedded", {}).get("showtimes", []) or data.get("showtimes", [])
                log(f"  Got {len(showtimes)} showtimes from intercepted API")

                for st in showtimes:
                    title     = st.get("movieName", "")
                    title_low = title.lower()
                    attrs     = " ".join(str(a) for a in st.get("attributeIds", [])).lower()
                    desc      = st.get("description", "").lower()
                    combined  = title_low + " " + attrs + " " + desc

                    is_watchlist = any(w in title_low for w in WATCHLIST)
                    is_large     = any(k in combined for k in FORMAT_KEYWORDS)

                    if is_watchlist:
                        log(f"  '{title}' | large={is_large} | attrs='{attrs[:60]}'")

                    if is_watchlist and is_large:
                        results.append({
                            "title":        title,
                            "showtime":     st.get("showDateTimeLocal", ""),
                            "showtime_id":  str(st.get("id", "")),
                            "seats_avail":  st.get("seatsAvailable", 99),
                            "total_seats":  st.get("totalSeats", 0),
                            "purchase_url": f"https://www.amctheatres.com{st.get('purchaseUrl','')}",
                        })
                        log(f"  ✓ MATCH: {title}")

            else:
                # Fall back to parsing the rendered page HTML
                log("  No API intercept — parsing rendered page...")
                content = page.content()

                # Look for movie titles in the rendered HTML
                from bs4 import BeautifulSoup
                soup      = BeautifulSoup(content, "html.parser")
                page_text = soup.get_text().lower()

                for w in WATCHLIST:
                    if w in page_text:
                        log(f"  Page contains '{w}' ✓")

                # Try to find showtime data in page source (often embedded as JSON)
                import re
                json_matches = re.findall(r'window\.__INITIAL_STATE__\s*=\s*({.+?});', content)
                if not json_matches:
                    json_matches = re.findall(r'"showtimes"\s*:\s*(\[.+?\])', content[:50000])

                if json_matches:
                    log(f"  Found embedded JSON data")
                    try:
                        embedded = json.loads(json_matches[0])
                        log(f"  Embedded data keys: {list(embedded.keys())[:5] if isinstance(embedded, dict) else 'array'}")
                    except:
                        pass
                else:
                    log("  No embedded JSON found — checking visible text...")
                    page_text = soup.get_text(separator=" ", strip=True)
                    for w in WATCHLIST[:5]:
                        if w in page_text.lower():
                            log(f"  Page contains '{w}' ✓")
                    clean = " ".join(page_text.split())
                    log(f"  Page text: {clean[100:500]}")

            browser.close()

    except Exception as e:
        log(f"  Playwright error: {e}")
        import traceback
        traceback.print_exc()

    return results

def check_sweet_spot(purchase_url):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page()
            page.goto(purchase_url, wait_until="networkidle", timeout=30000)
            content = page.content()
            browser.close()

        from bs4 import BeautifulSoup
        soup         = BeautifulSoup(content, "html.parser")
        all_seats    = soup.find_all(attrs={"data-row": True})
        total_cap    = len(all_seats)
        sweet_by_row = {}
        for seat in soup.find_all(attrs={"data-row": True, "data-status": "available"}):
            row = seat.get("data-row", "").upper()
            col = int(seat.get("data-column", 0))
            if row in SWEET_ROWS and col > 2:
                sweet_by_row.setdefault(row, []).append(col)
        good_rows = []
        for row, cols in sweet_by_row.items():
            sc = sorted(cols)
            for i in range(len(sc) - 1):
                if sc[i+1] - sc[i] == 1:
                    good_rows.append(row)
                    break
        if good_rows:
            rng = f"Row {good_rows[0]}" if len(good_rows) == 1 else f"Rows {min(good_rows)}–{max(good_rows)}"
            return True, f"{rng} · {len(good_rows)} row(s) with adjacent pairs", total_cap
        if not all_seats:
            return True, "Sweet spot availability unverified — check link", 0
    except Exception as e:
        log(f"  Seat map error: {e}")
        return True, "Sweet spot unverified (seat map unavailable)", 0
    return False, "", 0

def fullness_label(avail, total):
    if total <= 0:
        return "Availability unknown", 0
    pct_sold = round((1 - avail / total) * 100)
    if pct_sold < 5:    label = "Just went on sale — almost all seats available"
    elif pct_sold < 25: label = "Mostly open"
    elif pct_sold < 50: label = "Filling up"
    elif pct_sold < 75: label = "More than half sold"
    elif pct_sold < 90: label = "Nearly sold out"
    else:               label = "Almost gone"
    return label, pct_sold

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
            show_display = showtime_str or "See link for showtime"

        status_label, pct_sold = fullness_label(seats_avail, total_seats)
        bar_w   = 24
        filled  = round(bar_w * pct_sold / 100)

        if is_return:
            context = f"Previously sold out — {seats_avail} seats just opened up"
        elif first_seen_seats and first_seen_seats != seats_avail:
            context = f"Was {first_seen_seats} seats, now {seats_avail} remaining"
        else:
            context = f"{seats_avail} of {total_seats if total_seats else '?'} seats available"

        if is_return:
            ctx_color, ctx_text = "#7eb8a0", f"⟳ RETURN — {seats_avail} seats just opened"
        else:
            ctx_color, ctx_text = "#e8c547", context

        text = f"""
IMAX 70MM SEATS — {title.upper()}
{"─"*42}
{show_display} · AMC Lincoln Square 13
SEATS: {context}
ZONE:  {seat_info}
FILL:  {pct_sold}% sold — {status_label}
→ BOOK: {purchase_url}
{"─"*42}
        """.strip()

        html = f"""
<html><body style="font-family:monospace;background:#0a0a08;color:#e8e0cc;padding:32px;max-width:500px;margin:0 auto;">
  <div style="border-left:3px solid #e8c547;padding-left:20px;margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:.3em;color:rgba(232,197,71,.5);margin-bottom:8px;">AMC LINCOLN SQUARE · IMAX 70MM</div>
    <div style="font-size:26px;font-weight:900;letter-spacing:.06em;color:#e8c547;">{title.upper()}</div>
    <div style="font-size:13px;color:rgba(255,255,255,.45);margin-top:4px;">{show_display}</div>
  </div>
  <div style="background:rgba(232,197,71,.06);border:1px solid rgba(232,197,71,.18);padding:16px;margin-bottom:16px;">
    <div style="font-size:9px;letter-spacing:.22em;color:rgba(232,197,71,.55);margin-bottom:6px;">⬡ SWEET SPOT SEATS</div>
    <div style="font-size:13px;color:#e8c547;">{seat_info}</div>
    <div style="font-size:10px;color:rgba(255,255,255,.35);margin-top:4px;">2 adjacent seats · rows F–L · away from edges</div>
  </div>
  <div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);padding:16px;margin-bottom:20px;">
    <div style="font-size:9px;letter-spacing:.22em;color:rgba(255,255,255,.3);margin-bottom:10px;">THEATER FILL</div>
    <div style="height:6px;border-radius:2px;overflow:hidden;margin-bottom:8px;background:rgba(255,255,255,.08);">
      <div style="display:inline-block;width:{pct_sold}%;height:100%;background:#e8c547;vertical-align:top;"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:10px;margin-bottom:6px;">
      <span style="color:{ctx_color};">{ctx_text}</span>
      <span style="color:rgba(255,255,255,.3);">{pct_sold}% sold</span>
    </div>
    <div style="font-size:11px;color:rgba(255,255,255,.35);">{status_label}</div>
  </div>
  <a href="{purchase_url}" style="display:block;background:#e8c547;color:#0a0a08;text-align:center;padding:14px;font-size:11px;font-weight:900;letter-spacing:.3em;text-decoration:none;margin-bottom:20px;">BOOK NOW →</a>
  <div style="font-size:9px;color:rgba(255,255,255,.18);letter-spacing:.1em;line-height:1.9;">
    One-time alert · Will re-alert only if show sells out then gets returns<br>
    AMC Lincoln Square 13 · IMAX 70MM
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
    log("─── Scanning AMC Lincoln Square for IMAX 70MM...")
    showtimes = get_imax_showtimes()
    if not showtimes:
        log("No matching IMAX 70MM showtimes found.")
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

        if avail == 0:
            state["ever_sold_out"] = True
        state["last_seen_seats"] = avail
        if total > 0:
            state["capacity"] = total

        if avail < MIN_SEATS:
            log(f"  Skip — only {avail} seat(s): {show['title']}")
            SHOWTIME_STATE[sid] = state
            save_state()
            continue

        if state["alerted"] and not is_return:
            log(f"  Skip — already alerted: {show['title']}")
            SHOWTIME_STATE[sid] = state
            save_state()
            continue

        if is_return:
            log(f"  Return after sellout: {show['title']}")
            state["alerted"] = False

        log(f"  Checking seat map: {show['title']}")
        good, seat_info, detected_cap = check_sweet_spot(show["purchase_url"])
        cap = state["capacity"] or detected_cap or total

        if good:
            sent = send_alert(
                title=show["title"],
                showtime_str=show["showtime"],
                seat_info=seat_info,
                purchase_url=show["purchase_url"],
                seats_avail=avail,
                total_seats=cap,
                first_seen_seats=state["first_seen_seats"],
                is_return=is_return,
            )
            if sent:
                state["alerted"] = True
        else:
            log(f"  No sweet spot seats: {show['title']}")

        SHOWTIME_STATE[sid] = state
        save_state()

def main():
    once = "--once" in sys.argv
    log("🎬 IMAX 70MM Seat Watcher started")
    log(f"   Mode:      {'single scan' if once else 'continuous'}")
    log(f"   Watching:  {', '.join(WATCHLIST[:4])}...")
    log(f"   Alerting:  {ALERT_EMAIL}")
    load_state()

    if once:
        scan()
    else:
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
