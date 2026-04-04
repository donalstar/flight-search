#!/usr/bin/env python3
"""
Flight search tool — Alaska Airlines first class nonstop SFO↔JFK.
Searches the next 2 months and generates flights.html.

Usage:
    python flight_search.py
"""

import subprocess
import sys
import time
from datetime import date, datetime, timedelta
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
OUTPUT_PATH = Path(__file__).parent / "flights.html"
_EC2_HOST   = "ec2-user@44.215.215.217"
_EC2_PEM    = Path.home() / ".ssh" / "portfolio-tracker.pem"
_EC2_DEST   = "/usr/share/nginx/html/flights"

ROUTES = [
    (Airport.SFO, Airport.JFK, "SFO → JFK"),
    (Airport.SFO, Airport.EWR, "SFO → EWR"),
    (Airport.JFK, Airport.SFO, "JFK → SFO"),
    (Airport.EWR, Airport.SFO, "EWR → SFO"),
]


def _date_str(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _fmt_duration(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60}m"


def scan_route(dep: Airport, arr: Airport, label: str) -> list[dict]:
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
        seat_type=SeatType.FIRST,
        stops=MaxStops.NON_STOP,
        airlines=[ALASKA],
        from_date=_date_str(start),
        to_date=_date_str(end),
    )

    date_results = SearchDates().search(date_filters)
    if not date_results:
        print(f"  No dates found for {label}")
        return []

    # date_results is list[DatePrice]; .date is a tuple, .price is float
    # Build base rows from date scan
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
            "duration":   "—",
            "flight_num": "—",
            "price":      dp.price,
        })

    rows.sort(key=lambda r: r["price"])
    print(f"  Found {len(rows)} dates with prices — fetching details for cheapest 5...")

    # Phase 2: fetch details only for the 5 cheapest dates
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
            seat_type=SeatType.FIRST,
            stops=MaxStops.NON_STOP,
            airlines=[ALASKA],
        )
        for attempt in range(3):
            try:
                results = SearchFlights().search(flight_filters)
                if results:
                    best = results[0]
                    leg  = best.legs[0] if best.legs else None
                    row["dep_time"]   = leg.departure_datetime.strftime("%H:%M") if leg and leg.departure_datetime else "—"
                    row["arr_time"]   = leg.arrival_datetime.strftime("%H:%M")   if leg and leg.arrival_datetime   else "—"
                    row["duration"]   = _fmt_duration(best.duration) if best.duration else "—"
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

    # rows[:5] already have details fetched; sort all by date for display
    rows = sorted(rows, key=lambda r: r["date"])
    print(f"  Done.")
    return rows


def _price_color(price: float, avg: float) -> str:
    if price <= avg * 0.95:
        return "#2ecc71"
    if price >= avg * 1.05:
        return "#e74c3c"
    return "#e0e0e0"


def build_html(results_by_route: dict[str, list[dict]], generated_at: str, search_range: str = "") -> str:
    sections_html = ""

    for label, rows in results_by_route.items():
        if not rows:
            sections_html += f"""
            <section>
              <h2>{label}</h2>
              <p class="no-results">No first class nonstop Alaska flights found.</p>
            </section>"""
            continue

        prices = [r["price"] for r in rows if r["price"]]
        avg    = sum(prices) / len(prices) if prices else 0
        lo     = min(prices) if prices else 0
        hi     = max(prices) if prices else 0
        cheapest_row = min(rows, key=lambda r: r["price"])

        stat_html = f"""
        <div class="stats">
          <div class="stat"><div class="label">Dates Found</div><div class="value">{len(rows)}</div></div>
          <div class="stat"><div class="label">Cheapest</div><div class="value" style="color:#2ecc71">${lo:,.0f}</div></div>
          <div class="stat"><div class="label">Best Date</div><div class="value">{cheapest_row['date'].strftime('%b %-d')}</div></div>
          <div class="stat"><div class="label">Average</div><div class="value">${avg:,.0f}</div></div>
          <div class="stat"><div class="label">Most Expensive</div><div class="value" style="color:#e74c3c">${hi:,.0f}</div></div>
        </div>"""

        rows_html = ""
        for r in rows:
            color = _price_color(r["price"], avg) if r["price"] else "#e0e0e0"
            rows_html += (
                f"<tr>"
                f"<td>{r['date'].strftime('%b %-d, %Y')}</td>"
                f"<td>{r['day']}</td>"
                f"<td>{r['dep_time']}</td>"
                f"<td>{r['arr_time']}</td>"
                f"<td>{r['duration']}</td>"
                f"<td>{r['flight_num']}</td>"
                f"<td style='color:{color};font-weight:600'>${r['price']:,.0f}</td>"
                f"</tr>\n"
            )

        sections_html += f"""
        <section>
          <h2>{label}</h2>
          {stat_html}
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th><th>Day</th><th>Departs</th><th>Arrives</th>
                  <th>Duration</th><th>Flight</th><th>Price (USD)</th>
                </tr>
              </thead>
              <tbody>
                {rows_html}
              </tbody>
            </table>
          </div>
        </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Alaska Airlines First Class — SFO ↔ New York</title>
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
    h1 {{ font-size: 1.4rem; font-weight: 600; margin-bottom: 6px; }}
    .desc {{ font-size: 0.85rem; color: #aaa; margin-bottom: 4px; }}
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
  <h1>Alaska Airlines — First Class Nonstop — SFO ↔ New York</h1>
  <p class="desc">One-way fares per route (JFK &amp; EWR) · {search_range} · Nonstop · First Class · Departure times shown for 5 cheapest dates</p>
  <div class="updated">Generated {generated_at}</div>
  {sections_html}
</body>
</html>
"""


def main() -> None:
    today = date.today()
    start = today + timedelta(days=1)
    end   = today + timedelta(days=90)
    search_range = f"{start.strftime('%b %-d, %Y')} – {end.strftime('%b %-d, %Y')}"

    all_results = {}
    for dep, arr, label in ROUTES:
        print(f"\nScanning {label}...")
        rows = scan_route(dep, arr, label)
        all_results[label] = rows
        print(f"  {len(rows)} dates with flights")

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


if __name__ == "__main__":
    main()
