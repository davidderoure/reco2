# ORIGIN Trial CLI — TRE Setup and Reference

This guide covers installation, configuration, and day-to-day use of the
ORIGIN Trial command-line tools inside a Trusted Research Environment (TRE).

The workflow is split between two environments:

| Environment | Who | What runs here |
|-------------|-----|----------------|
| **Airlock** | Vinod (TRE operator) | `airlock_fetch.sh` — two curl commands that fetch data from the Trial API and save it as `data.json` |
| **VRE (virtual desktop)** | David (analyst) | `trial.analyse` and `trial.timeline` — read `data.json`, no internet access needed |

---

## Airlock — Vinod's steps

The airlock has outbound internet access but is flushed after each use.
The only things that need to go into the airlock are:

1. `trial/airlock_fetch.sh` — the fetch script (from the repo)
2. `ids_2.txt` — the participant OriginId list (provided by the study team)

Both files should be kept in **persistent storage** outside the airlock and
copied in each session.

### Running the fetch

Set the API credentials (obtain from David):

```bash
export TRIAL_CLIENT_ID=trial-api-m2m
export TRIAL_CLIENT_SECRET=your-secret-here
```

Run the fetch script:

```bash
bash airlock_fetch.sh ids_2.txt 2026-10-01 2026-10-08
```

Arguments: `<ids_file> <period_start> <period_end>` (dates in `YYYY-MM-DD`).

The script:
1. Obtains an OAuth2 access token from the ORIGIN auth server
2. Fetches engagement data for all participants in `ids_2.txt`
3. Saves the result as **`data.json`** in the current directory

Transfer `data.json` through the airlock into the VRE.

### ids_2.txt format

One OriginId per line (`XXXX-XXXX`, uppercase alphanumeric).
Blank lines and `#` comments are ignored:

```
# Usability trial participants
AB12-CD34
EF56-GH78
```

---

## VRE (virtual desktop) — David's steps

### One-time setup

```bash
# Clone the repository
git clone <repo-url> reco2
cd reco2

# Install Python dependencies
pip install -r requirements.txt
```

No credentials or internet access are needed for analysis — all commands read
from the `data.json` file that arrived through the airlock.

### Updating

```bash
cd reco2
git pull
pip install -r requirements.txt   # re-run if requirements.txt changed
```

### Running the analysis

```bash
# Full analysis report
python -m trial.analyse --input data.json

# Save report to file
python -m trial.analyse --input data.json --output report_$(date +%Y%m%d).txt

# Per-participant timeline
python -m trial.timeline --input data.json

# Timeline as HTML
python -m trial.timeline --input data.json --html timeline.html

# Filter to one participant
python -m trial.analyse --input data.json --participants AB12-CD34
python -m trial.timeline --input data.json --participants AB12-CD34
```

---

## Command reference

### trial.analyse(1)

**NAME**  
`trial.analyse` — analyse ORIGIN Trial recommender behaviour

**SYNOPSIS**  
```
python -m trial.analyse --input FILE [--participants ID ...] [--output FILE]
python -m trial.analyse (--days N | --from YYYY-MM-DD --to YYYY-MM-DD)
                        [--ids-file FILE] [--participants ID ...] [--output FILE]
```

**DESCRIPTION**  
Produces a plain-text report covering recommender health indicators:

- Recommender type distribution — are all strategies contributing?
- Story popularity across the population — any dominant stories?
- High-abort stories — cross-user escape signal for the story team
- Per-participant Q1 score trend — improving / flat / declining
- Per-participant recommender diversity — personalisation vs cold-start pattern
- Potential state-loss detection — reversion to cold-start mid-trial

**OPTIONS**  

| Option | Description |
|--------|-------------|
| `--input FILE` | Read from `data.json` (airlock output). No credentials needed. |
| `--days N` | Fetch live: last N days. |
| `--from YYYY-MM-DD` | Fetch live: start of period (requires `--to`). |
| `--to YYYY-MM-DD` | Fetch live: end of period. |
| `--ids-file FILE` | OriginId list file for live fetch (default: `ids/ids_2.txt`). |
| `--participants ID ...` | Filter to specific IDs (overrides `--ids-file`). |
| `--output FILE` | Write report to FILE instead of stdout. |

**EXAMPLES**  
```bash
# From airlock data
python -m trial.analyse --input data.json
python -m trial.analyse --input data.json --output report_$(date +%Y%m%d).txt

# Live fetch (requires credentials and internet)
python -m trial.analyse --days 30
python -m trial.analyse --from 2026-10-01 --to 2026-10-31
```

---

### trial.timeline(1)

**NAME**  
`trial.timeline` — per-participant engagement timeline

**SYNOPSIS**  
```
python -m trial.timeline --input FILE [--participants ID ...] [--html FILE]
python -m trial.timeline (--days N | --from YYYY-MM-DD --to YYYY-MM-DD)
                         [--ids-file FILE] [--participants ID ...] [--html FILE]
```

**DESCRIPTION**  
Displays each participant's engagement history as an ASCII timeline — one row
per session, with recommender types, Q1 scores, completion status, and flags
for state-loss reversion and high-abort stories.

The optional `--html` flag saves a self-contained HTML file (no external
network connections required to view it).

**OPTIONS**  

| Option | Description |
|--------|-------------|
| `--input FILE` | Read from `data.json` (airlock output). No credentials needed. |
| `--days N` | Fetch live: last N days. |
| `--from YYYY-MM-DD` | Fetch live: start of period. |
| `--to YYYY-MM-DD` | Fetch live: end of period. |
| `--ids-file FILE` | OriginId list file for live fetch (default: `ids/ids_2.txt`). |
| `--participants ID ...` | Filter to specific IDs (overrides `--ids-file`). |
| `--html FILE` | Also save an HTML version. |

**EXAMPLES**  
```bash
# From airlock data
python -m trial.timeline --input data.json
python -m trial.timeline --input data.json --html timeline.html
python -m trial.timeline --input data.json --participants AB12-CD34

# Live fetch (requires credentials and internet)
python -m trial.timeline --days 30
```

---

### trial.fetch(1)

**NAME**  
`trial.fetch` — fetch raw ORIGIN Trial API engagement data

**SYNOPSIS**  
```
python -m trial.fetch (--days N | --from YYYY-MM-DD --to YYYY-MM-DD)
                      [--ids-file FILE] [--participants ID ...]
                      [--format summary|json|csv] [--output FILE]
```

**DESCRIPTION**  
Fetches raw engagement data from the Trial API. This tool always calls the
API directly and requires credentials. Use `airlock_fetch.sh` instead when
running from the airlock.

**OPTIONS**  

| Option | Description |
|--------|-------------|
| `--days N` | Last N days. |
| `--from YYYY-MM-DD` | Start of period (requires `--to`). |
| `--to YYYY-MM-DD` | End of period. |
| `--ids-file FILE` | OriginId list file (default: `ids/ids_2.txt`). |
| `--participants ID ...` | Filter to specific IDs. |
| `--format FORMAT` | `summary` (default), `json`, or `csv`. |
| `--output FILE` | Write to FILE instead of stdout. |

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `Error: Failed to obtain access token` | Wrong credentials | Check `TRIAL_CLIENT_ID` / `TRIAL_CLIENT_SECRET` with David |
| `Error: IDs file not found` | `ids_2.txt` not in airlock | Copy the file in from persistent storage |
| `Input file not found: data.json` | `data.json` not yet transferred | Run the airlock fetch first and transfer the file |
| `HTTP 401: Unauthorized` | Token rejected | Verify credentials |
| `HTTP 403: Forbidden` | Account lacks `trial_api_user` role | Contact back-end dev |

For other errors send the full terminal output to David.
