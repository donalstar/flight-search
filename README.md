# flight-search

Searches Alaska Airlines first class nonstop flights between SFO and New York airports (JFK and EWR) for the next 90 days, and publishes the results as a dark-themed responsive HTML page.

Live at: **https://portfolio.runguru.net/flights**

## Architecture

```
flight_search.py       # main script
run_cron.sh            # cron wrapper (runs daily at 6am)
logs/run.log           # cron output log
flights.html           # generated HTML (gitignored)
```

### How it works

1. **Date scan** — calls Google Flights (via the `fli` library) once per route to get all dates with available fares across the 90-day window (2 calls per route)
2. **Detail fetch** — for the 5 cheapest dates per route, fetches full flight details: departure/arrival times, flight number, confirmed price (8s delay between calls to avoid rate limits)
3. **HTML generation** — builds a responsive dark-themed page with stat cards (cheapest, average, most expensive) and a results table per route
4. **Publish** — SCPs `flights.html` to EC2 nginx at `/usr/share/nginx/html/flights`

### Routes searched

| Route | Airports |
|-------|----------|
| SFO → New York | SFO → JFK, SFO → EWR |
| New York → SFO | JFK → SFO, EWR → SFO |

### Filters

- Airline: Alaska Airlines only
- Cabin: First Class
- Stops: Nonstop only
- Trip type: One-way
- Passengers: 1 adult

## Setup

### Prerequisites

- Python 3.10+
- SSH key at `~/.ssh/portfolio-tracker.pem` (for EC2 publish)

### Install

```bash
git clone https://github.com/donalstar/flight-search.git
cd flight-search
python3 -m venv .venv
source .venv/bin/activate
pip install git+https://github.com/punitarani/fli.git
```

### Run manually

```bash
source .venv/bin/activate
python3 flight_search.py
```

Output is written to `flights.html` and published to EC2.

### Cron (daily at 6am)

```bash
crontab -e
# Add:
0 6 * * * /bin/bash "/Users/donal/flight-search/run_cron.sh"
```

## Dependencies

- [`fli`](https://github.com/punitarani/fli) — reverse-engineers Google Flights via `curl_cffi`; no API key required

## Notes

- Google Flights rate-limits aggressively. The script fetches full details only for the 5 cheapest dates per route to stay within limits; all other dates show price only.
- The `fli` library may break if Google changes their internal API.
- Alaska nonstop first class on SFO↔EWR may have limited or no availability.
