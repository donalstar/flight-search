#!/usr/bin/env python3
"""
Flight search tool — SFO ↔ New York (JFK & EWR).
Searches two cabins and generates a tabbed flights.html.

Usage:
    python flight_search.py
"""

import json
import smtplib
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from fli.models import (
    Airline,
    Airport,
    DateSearchFilters,
    FlightSearchFilters,
    FlightSegment,
    MaxStops,
    PassengerInfo,
    SeatType,
    TripType,
)
from fli.search import SearchDates, SearchFlights

ALASKA      = Airline["AS"]
UNITED      = Airline["UA"]
OUTPUT_PATH = Path(__file__).parent / "flights.html"
_EC2_HOST   = "ec2-user@54.164.139.134"
_EC2_PEM    = Path.home() / ".ssh" / "portfolio-tracker.pem"
_EC2_DEST   = "/usr/share/nginx/html/flights.html"  # served at flights.runguru.net
_CONFIG     = Path(__file__).parent / "config.json"
_TO_EMAIL   = "donalstar@gmail.com"

ROUTES = [
    (Airport.SFO, Airport.JFK, "SFO → JFK"),
    (Airport.SFO, Airport.EWR, "SFO → EWR"),
    (Airport.JFK, Airport.SFO, "JFK → SFO"),
    (Airport.EWR, Airport.SFO, "EWR → SFO"),
]

SEARCHES = [
    {
        "id":        "alaska",
        "tab":       "Alaska Airlines — First Class",
        "seat_type": SeatType.FIRST,
        "airlines":  [ALASKA],
        "desc":      "Alaska Airlines · Nonstop · First Class · 1 passenger",
    },
    {
        "id":        "business",
        "tab":       "All Airlines — Business Class",
        "seat_type": SeatType.BUSINESS,
        "airlines":  None,
        "desc":      "All Airlines · Nonstop · Business Class · 1 passenger",
    },
    {
        "id":        "united_pe",
        "tab":       "United — Premium Economy",
        "seat_type": SeatType.PREMIUM_ECONOMY,
        "airlines":  [UNITED],
        "desc":      "United Airlines · Nonstop · Premium Economy · 1 passenger",
    },
]


def _date_str(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _fmt_duration(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60}m"


def scan_route(dep: Airport, arr: Airport, label: str,
               seat_type: SeatType, airlines: list | None) -> list[dict]:
    today = date.today()
    start = today + timedelta(days=1)
    end   = today + timedelta(days=90)

    print(f"  Phase 1: date scan {label} ({_date_str(start)} – {_date_str(end)})")
    date_filters = DateSearchFilters(
        trip_type=TripType.ONE_WAY,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[FlightSegment(
            departure_airport=[[dep, 0]],
            arrival_airport=[[arr, 0]],
            travel_date=_date_str(start),
        )],
        seat_type=seat_type,
        stops=MaxStops.NON_STOP,
        **({"airlines": airlines} if airlines else {}),
        from_date=_date_str(start),
        to_date=_date_str(end),
    )

    date_results = SearchDates().search(date_filters)
    if not date_results:
        print(f"  No dates found for {label}")
        return []

    rows = []
    for dp in date_results:
        if not dp.price or dp.price <= 0:
            continue
        d = dp.date[0].date() if hasattr(dp.date[0], "date") else dp.date[0]
        rows.append({
            "date":       d,
            "day":        d.strftime("%a"),
            "dep_time":   "—",
            "arr_time":   "—",
            "flight_num": "—",
            "price":      dp.price,
        })

    rows.sort(key=lambda r: r["price"])
    print(f"  Found {len(rows)} dates with prices — fetching details for cheapest 5...")

    for row in rows[:5]:
        print(f"    Fetching details for {row['date']}...")
        flight_filters = FlightSearchFilters(
            trip_type=TripType.ONE_WAY,
            passenger_info=PassengerInfo(adults=1),
            flight_segments=[FlightSegment(
                departure_airport=[[dep, 0]],
                arrival_airport=[[arr, 0]],
                travel_date=_date_str(row["date"]),
            )],
            seat_type=seat_type,
            stops=MaxStops.NON_STOP,
            **({"airlines": airlines} if airlines else {}),
        )
        for attempt in range(3):
            try:
                results = SearchFlights().search(flight_filters)
                if results:
                    best = results[0]
                    leg  = best.legs[0] if best.legs else None
                    row["dep_time"]   = leg.departure_datetime.strftime("%H:%M") if leg and leg.departure_datetime else "—"
                    row["arr_time"]   = leg.arrival_datetime.strftime("%H:%M")   if leg and leg.arrival_datetime   else "—"
                    row["flight_num"] = f"{leg.airline} {leg.flight_number}"     if leg else "—"
                    row["price"]      = best.price
                break
            except Exception as exc:
                if "429" in str(exc):
                    wait = 30 * (attempt + 1)
                    print(f"    Rate limited — waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"    Error: {exc}")
                    break
        time.sleep(8)

    rows = sorted(rows, key=lambda r: r["date"])
    print(f"  Done.")
    return rows


def _price_color(price: float, avg: float) -> str:
    if price <= avg * 0.95:
        return "#2ecc71"
    if price >= avg * 1.05:
        return "#e74c3c"
    return "#e0e0e0"


def _build_tab_sections(results_by_route: dict[str, list[dict]], no_results_msg: str) -> str:
    sections_html = ""
    for label, rows in results_by_route.items():
        if not rows:
            sections_html += f"""
            <section>
              <h2>{label}</h2>
              <p class="no-results">{no_results_msg}</p>
            </section>"""
            continue

        prices       = [r["price"] for r in rows if r["price"]]
        avg          = sum(prices) / len(prices) if prices else 0
        lo           = min(prices) if prices else 0
        hi           = max(prices) if prices else 0
        cheapest_row = min(rows, key=lambda r: r["price"])

        rows_html = ""
        for r in rows:
            color = _price_color(r["price"], avg) if r["price"] else "#e0e0e0"
            rows_html += (
                f"<tr>"
                f"<td>{r['date'].strftime('%b %-d, %Y')}</td>"
                f"<td>{r['day']}</td>"
                f"<td>{r['flight_num']}</td>"
                f"<td>{r['dep_time']}</td>"
                f"<td>{r['arr_time']}</td>"
                f"<td style='color:{color};font-weight:600'>${r['price']:,.0f}</td>"
                f"</tr>\n"
            )

        sections_html += f"""
        <section>
          <h2>{label}</h2>
          <div class="stats">
            <div class="stat"><div class="label">Dates Found</div><div class="value">{len(rows)}</div></div>
            <div class="stat"><div class="label">Cheapest</div><div class="value" style="color:#2ecc71">${lo:,.0f}</div></div>
            <div class="stat"><div class="label">Best Date</div><div class="value">{cheapest_row['date'].strftime('%b %-d')}</div></div>
            <div class="stat"><div class="label">Average</div><div class="value">${avg:,.0f}</div></div>
            <div class="stat"><div class="label">Most Expensive</div><div class="value" style="color:#e74c3c">${hi:,.0f}</div></div>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th><th>Day</th><th>Flight</th>
                  <th>Departs</th><th>Arrives</th><th>Price (USD)</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>
          </div>
        </section>"""
    return sections_html


def build_html(all_results: dict[str, dict[str, list[dict]]],
               generated_at: str, search_range: str = "") -> str:
    tabs_html    = ""
    panels_html  = ""

    for i, search in enumerate(SEARCHES):
        sid      = search["id"]
        tab_label = search["tab"]
        desc     = search["desc"]
        active   = "active" if i == 0 else ""
        sections = _build_tab_sections(
            all_results[sid],
            f"No nonstop flights found.",
        )
        tabs_html += f'<button class="tab {active}" onclick="switchTab(\'{sid}\')" id="tab-{sid}">{tab_label}</button>\n'
        panels_html += f"""
        <div class="panel {active}" id="panel-{sid}">
          <p class="desc">{desc} · {search_range} · Departure times shown for 5 cheapest dates</p>
          {sections}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <title>Flights — SFO ↔ New York</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f1117;
      color: #e0e0e0;
      padding: 24px 16px;
      max-width: 960px;
      margin: 0 auto;
    }}
    h1 {{ font-size: 1.4rem; font-weight: 600; margin-bottom: 16px; }}
    .tabs {{ display: flex; gap: 8px; margin-bottom: 28px; flex-wrap: wrap; }}
    .tab {{
      padding: 8px 18px;
      border-radius: 8px;
      border: 1px solid #2a2d3a;
      background: #1a1d27;
      color: #888;
      font-size: 0.85rem;
      cursor: pointer;
      transition: all .15s;
    }}
    .tab:hover {{ color: #e0e0e0; border-color: #444; }}
    .tab.active {{ background: #2a2d3a; color: #e0e0e0; border-color: #555; font-weight: 600; }}
    .panel {{ display: none; }}
    .panel.active {{ display: block; }}
    .desc {{ font-size: 0.82rem; color: #aaa; margin-bottom: 4px; }}
    .updated {{ font-size: 0.75rem; color: #666; margin-bottom: 32px; }}
    section {{ margin-bottom: 48px; }}
    h2 {{ font-size: 1.1rem; font-weight: 600; margin-bottom: 16px; color: #ccc; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .stat {{
      background: #1a1d27;
      border-radius: 10px;
      padding: 16px;
      text-align: center;
    }}
    .stat .label {{ font-size: 0.72rem; color: #888; text-transform: uppercase;
                    letter-spacing: .05em; margin-bottom: 6px; }}
    .stat .value {{ font-size: 1.4rem; font-weight: 700; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
    th {{
      text-align: left; padding: 8px 12px;
      border-bottom: 1px solid #2a2d3a;
      font-size: 0.72rem; text-transform: uppercase;
      letter-spacing: .05em; color: #888;
    }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #1e2130; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #1a1d27; }}
    .no-results {{ color: #666; font-size: 0.9rem; }}
    @media (max-width: 480px) {{
      .stat .value {{ font-size: 1.1rem; }}
    }}
  </style>
</head>
<body>
  <h1>Flights — SFO ↔ New York (JFK &amp; EWR)</h1>
  <div class="updated">Generated {generated_at}</div>
  <div class="tabs">
    {tabs_html}
  </div>
  {panels_html}
  <script>
    function switchTab(id) {{
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      document.getElementById('tab-' + id).classList.add('active');
      document.getElementById('panel-' + id).classList.add('active');
    }}
  </script>
</body>
</html>
"""


def _build_email_section(label: str, rows: list[dict]) -> str:
    top5 = sorted(rows, key=lambda r: r["price"])[:5]
    if not top5:
        return f"<h3 style='color:#ccc;font-size:0.95rem;margin:20px 0 8px'>{label}</h3><p style='color:#888'>No flights found.</p>"
    rows_html = "".join(
        f"<tr>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #2a2d3a'>{r['date'].strftime('%b %-d, %Y')}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #2a2d3a'>{r['day']}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #2a2d3a'>{r['flight_num']}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #2a2d3a'>{r['dep_time']}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #2a2d3a'>{r['arr_time']}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #2a2d3a;color:#2ecc71;font-weight:600'>${r['price']:,.0f}</td>"
        f"</tr>"
        for r in top5
    )
    return f"""
    <h3 style='color:#ccc;font-size:0.95rem;margin:20px 0 8px'>{label}</h3>
    <table style='width:100%;border-collapse:collapse;font-size:0.82rem'>
      <thead><tr style='color:#888;font-size:0.7rem;text-transform:uppercase'>
        <th style='padding:6px 10px;border-bottom:1px solid #2a2d3a;text-align:left'>Date</th>
        <th style='padding:6px 10px;border-bottom:1px solid #2a2d3a;text-align:left'>Day</th>
        <th style='padding:6px 10px;border-bottom:1px solid #2a2d3a;text-align:left'>Flight</th>
        <th style='padding:6px 10px;border-bottom:1px solid #2a2d3a;text-align:left'>Departs</th>
        <th style='padding:6px 10px;border-bottom:1px solid #2a2d3a;text-align:left'>Arrives</th>
        <th style='padding:6px 10px;border-bottom:1px solid #2a2d3a;text-align:left'>Price</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>"""


def _build_email_html(all_results: dict[str, dict[str, list[dict]]], generated_at: str) -> str:
    body = ""
    for search in SEARCHES:
        sid = search["id"]
        body += f"<h2 style='color:#e0e0e0;font-size:1rem;margin:28px 0 4px;border-bottom:1px solid #2a2d3a;padding-bottom:8px'>{search['tab']}</h2>"
        for label, rows in all_results[sid].items():
            body += _build_email_section(label, rows)

    return f"""
    <div style='font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
                background:#0f1117;color:#e0e0e0;padding:24px;max-width:700px;margin:0 auto'>
      <h1 style='font-size:1.3rem;font-weight:600;margin-bottom:4px'>Flights — SFO ↔ New York</h1>
      <p style='font-size:0.78rem;color:#888;margin-bottom:4px'>5 cheapest fares per route · Nonstop · 1 passenger · {generated_at}</p>
      {body}
      <p style='margin-top:24px;font-size:0.75rem;color:#555'>
        Full results: <a href='https://portfolio.runguru.net/flights' style='color:#4a9eff'>portfolio.runguru.net/flights</a>
      </p>
    </div>"""


def _send_email(html: str, generated_at: str) -> None:
    if not _CONFIG.exists():
        print("  [email] No config.json found, skipping.", file=sys.stderr)
        return
    cfg = json.loads(_CONFIG.read_text())
    gmail_user   = cfg.get("gmail_user", "")
    app_password = cfg.get("gmail_app_password", "")
    if not app_password:
        print("  [email] gmail_app_password not set, skipping.", file=sys.stderr)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Flights SFO↔NYC — {generated_at}"
    msg["From"]    = gmail_user
    msg["To"]      = _TO_EMAIL
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, app_password)
            server.sendmail(gmail_user, _TO_EMAIL, msg.as_string())
        print(f"  [email] Sent to {_TO_EMAIL}")
    except Exception as exc:
        print(f"  [email] Failed: {exc}", file=sys.stderr)


def main() -> None:
    today = date.today()
    start = today + timedelta(days=1)
    end   = today + timedelta(days=90)
    search_range = f"{start.strftime('%b %-d, %Y')} – {end.strftime('%b %-d, %Y')}"

    all_results: dict[str, dict[str, list[dict]]] = {}
    for search in SEARCHES:
        sid = search["id"]
        print(f"\n=== {search['tab']} ===")
        all_results[sid] = {}
        for dep, arr, label in ROUTES:
            print(f"\nScanning {label}...")
            rows = scan_route(dep, arr, label, search["seat_type"], search["airlines"])
            all_results[sid][label] = rows

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = build_html(all_results, generated_at, search_range)
    OUTPUT_PATH.write_text(html)
    print(f"\nWritten to {OUTPUT_PATH}")

    try:
        subprocess.run(
            ["scp", "-i", str(_EC2_PEM), "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=10", str(OUTPUT_PATH), f"{_EC2_HOST}:{_EC2_DEST}"],
            check=True, capture_output=True,
        )
        print("Published to portfolio.runguru.net/flights")
    except Exception as exc:
        print(f"Failed to publish to EC2: {exc}", file=sys.stderr)

    email_html = _build_email_html(all_results, generated_at)
    _send_email(email_html, generated_at)


if __name__ == "__main__":
    main()
