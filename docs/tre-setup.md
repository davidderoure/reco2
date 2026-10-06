# ORIGIN Trial CLI — TRE Setup and Reference

This guide covers installation, configuration, and day-to-day use of the
ORIGIN Trial command-line tools on a virtual Linux desktop inside a Trusted
Research Environment (TRE).

---

## 1. Prerequisites

- Python 3.10 or later (`python3 --version`)
- Git access to the `reco2` repository
- API credentials (`TRIAL_CLIENT_ID` and `TRIAL_CLIENT_SECRET`) — obtain from
  David De Roure
- A file of participant OriginIds — obtain from the study team via the data
  sharing agreement (format described in §4 below)

---

## 2. Installation

```bash
# Clone the repository
git clone <repo-url> reco2
cd reco2

# Install Python dependencies
pip install -r requirements.txt
```

Dependencies are listed in `requirements.txt`; the trial client requires only
`requests` in addition to the standard library.

---

## 3. Configuration

Credentials can be set in two ways.

### Option A — environment variables (recommended for scripts)

```bash
export TRIAL_CLIENT_ID=trial-api-m2m
export TRIAL_CLIENT_SECRET=<secret>
```

Add these to `~/.bashrc` or `~/.profile` so they are set automatically.

### Option B — `.env` file (convenient for interactive use)

Create a file called `.env` in the `reco2/` directory:

```
TRIAL_CLIENT_ID=trial-api-m2m
TRIAL_CLIENT_SECRET=<secret>
```

The tools load this file automatically. **Do not commit `.env` to git** — it
is already listed in `.gitignore`.

Authentication uses OAuth2 client credentials (M2M); tokens expire after
5 minutes and are refreshed automatically.

---

## 4. Participant ID file

The Trial API requires an explicit list of OriginIds. Create a plain-text file
in the `ids/` directory:

```
ids/ids_2.txt      ← usability trial participants (current phase)
```

Format: one OriginId per line (`XXXX-XXXX`, uppercase alphanumeric).
Blank lines and lines beginning with `#` are ignored.

```
# Usability trial participants
AB12-CD34
EF56-GH78
```

The `ids/` directory is gitignored — files placed there will not be committed
to the repository. Obtain the current participant list from the study team.

---

## 5. Updating

```bash
cd reco2
git pull
pip install -r requirements.txt   # re-run if requirements.txt changed
```

All commands are run from the `reco2/` directory.

---

## 6. Command reference

All commands are invoked as Python modules from the `reco2/` directory:

```bash
python -m trial.analyse  ...
python -m trial.fetch    ...
python -m trial.timeline ...
```

---

### trial.analyse(1)

**NAME**  
`trial.analyse` — analyse ORIGIN Trial recommender behaviour

**SYNOPSIS**  
```
python -m trial.analyse (--days N | --from YYYY-MM-DD --to YYYY-MM-DD)
                        [--ids-file FILE] [--participants ID ...]
                        [--output FILE]
```

**DESCRIPTION**  
Fetches engagement data from the Trial API and produces a plain-text report
covering recommender health indicators for the study team:

- Recommender type distribution — are all strategies contributing?
- Story popularity across the population — any dominant stories?
- High-abort stories — cross-user escape signal for the story team
- Per-participant Q1 score trend — improving / flat / declining
- Per-participant recommender diversity — personalisation vs cold-start pattern
- Potential state-loss detection — reversion to cold-start mid-trial

**OPTIONS**  

| Option | Description |
|--------|-------------|
| `--days N` | Analyse the last N days (ending now). Required unless `--from` is given. |
| `--from YYYY-MM-DD` | Start of period. Requires `--to`. |
| `--to YYYY-MM-DD` | End of period. Required with `--from`. |
| `--ids-file FILE` | Path to OriginId list file (default: `ids/ids_2.txt`). |
| `--participants ID ...` | Space-separated OriginIds. Overrides `--ids-file`. |
| `--output FILE` | Write report to FILE instead of stdout. |

**EXAMPLES**  
```bash
# Daily monitoring — last 30 days to stdout
python -m trial.analyse --days 30

# Save weekly report to file
python -m trial.analyse --days 7 --output report_$(date +%Y%m%d).txt

# Full trial period
python -m trial.analyse --from 2026-10-01 --to 2026-11-30

# Single participant
python -m trial.analyse --days 30 --participants AB12-CD34

# Use a specific ID file
python -m trial.analyse --days 30 --ids-file ids/ids_2.txt
```

---

### trial.timeline(1)

**NAME**  
`trial.timeline` — per-participant engagement timeline

**SYNOPSIS**  
```
python -m trial.timeline (--days N | --from YYYY-MM-DD --to YYYY-MM-DD)
                         [--ids-file FILE] [--participants ID ...]
                         [--html FILE]
```

**DESCRIPTION**  
Displays each participant's engagement history as an ASCII timeline — one row
per session, with recommender types, Q1 scores, completion status, and flags
for state-loss reversion and high-abort stories.

Designed for terminal use in a TRE. The optional `--html` flag saves a
self-contained HTML file for richer local review (no external network
connections required to view it).

**OPTIONS**  

| Option | Description |
|--------|-------------|
| `--days N` | Show the last N days. |
| `--from YYYY-MM-DD` | Start of period. |
| `--to YYYY-MM-DD` | End of period. |
| `--ids-file FILE` | Path to OriginId list file (default: `ids/ids_2.txt`). |
| `--participants ID ...` | Space-separated OriginIds. Overrides `--ids-file`. |
| `--html FILE` | Also save an HTML version of the timeline. |

**EXAMPLES**  
```bash
# All participants, last 30 days
python -m trial.timeline --days 30

# Save HTML for review
python -m trial.timeline --days 30 --html timeline.html

# Single participant drill-down
python -m trial.timeline --days 30 --participants AB12-CD34

# Date range
python -m trial.timeline --from 2026-10-01 --to 2026-10-31
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
Fetches raw engagement data from the Trial API. Useful for exporting data for
further analysis or archiving. The default format (`summary`) prints a
human-readable summary; `json` and `csv` produce machine-readable output.

**OPTIONS**  

| Option | Description |
|--------|-------------|
| `--days N` | Fetch the last N days (ending now). |
| `--from YYYY-MM-DD` | Start of period. Requires `--to`. |
| `--to YYYY-MM-DD` | End of period. Required with `--from`. |
| `--ids-file FILE` | Path to OriginId list file (default: `ids/ids_2.txt`). |
| `--participants ID ...` | Space-separated OriginIds. Overrides `--ids-file`. |
| `--format FORMAT` | Output format: `summary` (default), `json`, or `csv`. |
| `--output FILE` | Write output to FILE instead of stdout. |

**EXAMPLES**  
```bash
# Quick summary of last 7 days
python -m trial.fetch --days 7

# Export full month as CSV
python -m trial.fetch --from 2026-10-01 --to 2026-10-31 --format csv --output oct.csv

# Export as JSON for archiving
python -m trial.fetch --days 30 --format json --output data.json
```

---

## 7. Typical daily workflow

```bash
cd reco2

# Update the code
git pull

# Run the analysis report (last 7 days)
python -m trial.analyse --days 7 --output report_$(date +%Y%m%d).txt

# Review the timeline
python -m trial.timeline --days 7
```

---

## 8. Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `TRIAL_CLIENT_ID and TRIAL_CLIENT_SECRET must be set` | Credentials not configured | Set env vars or create `.env` (§3) |
| `ID list file not found: ids/ids_2.txt` | Participant ID file missing | Create `ids/ids_2.txt` (§4) |
| `Invalid OriginId` | Malformed ID in the file | Check format is `XXXX-XXXX` uppercase |
| `HTTP 401: Unauthorized` | Wrong credentials | Verify `TRIAL_CLIENT_ID` / `TRIAL_CLIENT_SECRET` with David |
| `HTTP 403: Forbidden` | Account lacks `trial_api_user` role | Contact back-end dev to grant the role |
| `HTTP 400: Bad request` | API parameter error | Check date formats (`YYYY-MM-DD`) and ID formats |

For other errors, run with the Python traceback visible (the tools print it
automatically on unexpected exceptions) and send the output to David.
