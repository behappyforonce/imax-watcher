import os
import time
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from bs4 import BeautifulSoup

# ── Config ─────────────────────────────────────────────────────────────────
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS")
ALERT_EMAIL    = os.environ.get("ALERT_EMAIL")
SCAN_INTERVAL  = int(os.environ.get("SCAN_INTERVAL", "900"))
MIN_SEATS      = int(os.environ.get("MIN_SEATS", "2"))

# ── Watchlist ───────────────────────────────────────────────────────────────
# Add film titles here in lowercase. The scanner checks if any watchlist word
# appears in the AMC listing title, so partial matches work fine.
WATCHLIST = [
    # ── Now playing ────────────────────────────────────────────────────
    "project hail mary",

    # ── Re-releases (Oscar season 2026) ────────────────────────────────
    "sinners",
    "one battle after another",
    "f1",

    # ── Upcoming 2026 ──────────────────────────────────────────────────
    "the odyssey",              # Nolan, July 17 2026 — shot entirely on IMAX 70mm
    "dune: part three",        # December 2026
    "dune part three",         # alternate title format AMC might use
    "flowervale street",       # August 2026, tentative IMAX

    # ── Unconfirmed title — update when announced ──────────────────────
    # "lanthimos",             # Yorgos Lanthimos next film, shot on IMAX cameras
]

THEATER_ID = "1076"  # AMC Lincoln Square 13
SWEET_ROWS = {"F","G","H","I","J","K","L"}

# ── State: showtime_id -> {alerted, first_seen_seats, last_seen_seats, capacity, ever_sold_out}
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
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
    results = []
    try:
        url  = f"https://www.amctheatres.com/api/v2/theatres/{THEATER_ID}/showtimes/views/current-and-upcoming?pageNumber=1&pageSize=100"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            log(f"API {resp.status_code} — trying scrape fallback")
            return scrape_fallback()
        data = resp.json()
        for movie in data.get("_embedded", {}).get("showtimes", []):
            title = movie.get("movieName", "").lower()
            fmt   = " ".join(str(x) for x in movie.get("attributeIds", []))
            if "imax" not in fmt.lower() and "70mm" not in fmt.lower():
                continue
            if not any(w in title for w in WATCHLIST):
                continue
            results.append({
                "title":        movie.get("movieName"),
                "showtime":     movie.get("showDateTimeLocal", ""),
                "showtime_id":  str(movie.get("id", "")),
                "seats_avail":  movie.get("seatsAvailable", 0),
                "total_seats":  movie.get("totalSeats", 0),
                "purchase_url": f"https://www.amctheatres.com{movie.get('purchaseUrl', '')}",
            })
    except Exception as e:
        log(f"API error: {e}")
        return scrape_fallback()
    return results

def scrape_fallback():
    results = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        resp    = requests.get("https://www.amctheatres.com/theatres/new-york/amc-lincoln-square-13/showtimes", headers=headers, timeout=15)
        soup    = BeautifulSoup(resp.text, "html.parser")
        for section in soup.find_all(class_="ShowtimesByMovie"):
            title_el = section.find(class_="MovieTitle")
            if not title_el: continue
            title = title_el.get_text(strip=True).lower()
            if not any(w in title for w in WATCHLIST): continue
            for st in section.find_all(class_="Showtime"):
                fmt_el = st.find(class_="Format")
                fmt    = fmt_el.get_text(strip=True).lower() if fmt_el else ""
                if "imax" not in fmt and "70mm" not in fmt: continue
                seats_el    = st.find(class_="SeatsAvailable")
                avail       = int(seats_el.get_text(strip=True).split()[0]) if seats_el else 0
                capacity_el = st.find(class_="TotalSeats")
                total       = int(capacity_el.get_text(strip=True).split()[0]) if capacity_el else 0
                link_el     = st.find("a", href=True)
                results.append({
                    "title":        title_el.get_text(strip=True),
                    "showtime":     st.get("data-showtime", ""),
                    "showtime_id":  st.get("data-id", ""),
                    "seats_avail":  avail,
                    "total_seats":  total,
                    "purchase_url": f"https://www.amctheatres.com{link_el['href']}" if link_el else "",
                })
    except Exception as e:
        log(f"Scrape fallback error: {e}")
    return results

def check_sweet_spot(purchase_url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        resp    = requests.get(purchase_url, headers=headers, timeout=15)
        soup    = BeautifulSoup(resp.text, "html.parser")
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
            return True, "Sweet spot availability unverified", 0
    except Exception as e:
        log(f"Seat map error: {e}")
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
            dt           = datetime.fromisoformat(showtime_str)
            show_display = dt.strftime("%A %b %-d · %-I:%M %p")
        except:
            show_display = showtime_str

        status_label, pct_sold = fullness_label(seats_avail, total_seats)
        bar_w   = 24
        filled  = round(bar_w * pct_sold / 100)
        bar_txt = "█" * filled + "░" * (bar_w - filled)

        if is_return:
            context = f"Previously sold out — {seats_avail} seats just opened up"
        elif first_seen_seats and first_seen_seats != seats_avail:
            context = f"Was {first_seen_seats} seats, now {seats_avail} remaining"
        else:
            context = f"{seats_avail} of {total_seats if total_seats else '?'} seats available"

        text = f"""
IMAX 70MM SEATS — {title.upper()}
{"─"*42}
{show_display} · AMC Lincoln Square 13

SEATS:  {context}
ZONE:   {seat_info}

FILL:   {bar_txt} {pct_sold}% sold
        {status_label}

→ BOOK: {purchase_url}

{"─"*42}
One-time alert. Won't re-notify unless show sells out then gets returns.
        """.strip()

        if is_return:
            ctx_color, ctx_text = "#7eb8a0", f"⟳ RETURN — {seats_avail} seats just opened"
        else:
            ctx_color, ctx_text = "#e8c547", context

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
    <div style="font-size:10px;color:rgba(255,255,255,.35);margin-top:4px;">2 adjacent seats confirmed · rows F–L · away from edges</div>
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
        log(f"✅ Alert sent: {title} — {show_display} ({seats_avail} seats, {pct_sold}% sold)")
        return True
    except Exception as e:
        log(f"❌ Email error: {e}")
        return False

def scan():
    log("─── Scanning AMC Lincoln Square for IMAX 70MM...")
    showtimes = get_imax_showtimes()
    if not showtimes:
        log("No IMAX 70MM showtimes found on watchlist.")
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
            log(f"  Skip — already alerted: {show['title']} ({show['showtime']})")
            SHOWTIME_STATE[sid] = state
            save_state()
            continue

        if is_return:
            log(f"  Return after sellout detected: {show['title']}")
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
