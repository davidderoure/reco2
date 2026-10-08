#!/usr/bin/env bash
# airlock_fetch.sh — fetch ORIGIN Trial engagement data from outside the VRE
#
# Runs two curl commands:
#   1. Obtain an OAuth2 access token
#   2. Fetch engagement data for all participants and save to data.json
#
# The resulting data.json is the file that passes through the airlock
# into the VRE, where it is analysed with:
#   python -m trial.analyse --input data.json
#   python -m trial.timeline --input data.json
#
# Usage:
#   ./airlock_fetch.sh <ids_file> <period_start> <period_end>
#
# Arguments:
#   ids_file       Path to the OriginId list file (one XXXX-XXXX per line)
#   period_start   Start of the reporting window, format: YYYY-MM-DD
#   period_end     End of the reporting window, format: YYYY-MM-DD
#
# Credentials — set as environment variables before running:
#   export TRIAL_CLIENT_ID=trial-api-m2m
#   export TRIAL_CLIENT_SECRET=...
#
# Example:
#   export TRIAL_CLIENT_ID=trial-api-m2m
#   export TRIAL_CLIENT_SECRET=your-secret-here
#   ./airlock_fetch.sh ids_2.txt 2026-10-01 2026-10-08
#
# The output file is written to data.json in the current directory.

set -euo pipefail

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
if [ $# -ne 3 ]; then
    echo "Usage: $0 <ids_file> <period_start> <period_end>" >&2
    echo "  Example: $0 ids_2.txt 2026-10-01 2026-10-08" >&2
    exit 1
fi

IDS_FILE="$1"
PERIOD_START="${2}T00:00:00Z"
PERIOD_END="${3}T23:59:59Z"

if [ ! -f "$IDS_FILE" ]; then
    echo "Error: IDs file not found: $IDS_FILE" >&2
    exit 1
fi

if [ -z "${TRIAL_CLIENT_ID:-}" ] || [ -z "${TRIAL_CLIENT_SECRET:-}" ]; then
    echo "Error: TRIAL_CLIENT_ID and TRIAL_CLIENT_SECRET must be set." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 1: Obtain access token
# ---------------------------------------------------------------------------
echo "Obtaining access token..."

TOKEN=$(curl -s -f \
    -X POST "https://auth.imagineear.com/realms/OriginWOB/protocol/openid-connect/token" \
    -d "grant_type=client_credentials" \
    -d "client_id=${TRIAL_CLIENT_ID}" \
    -d "client_secret=${TRIAL_CLIENT_SECRET}" \
    -d "scope=openid profile" \
    | grep -o '"access_token":"[^"]*"' \
    | cut -d'"' -f4)

if [ -z "$TOKEN" ]; then
    echo "Error: Failed to obtain access token. Check credentials." >&2
    exit 1
fi

echo "Token obtained."

# ---------------------------------------------------------------------------
# Step 2: Build originIds query string from the IDs file
# ---------------------------------------------------------------------------
ORIGIN_ID_PARAMS=""
while IFS= read -r line; do
    line="${line%%#*}"   # strip comments
    line="${line// /}"   # strip spaces
    [ -z "$line" ] && continue
    ORIGIN_ID_PARAMS="${ORIGIN_ID_PARAMS}&originIds=${line}"
done < "$IDS_FILE"

if [ -z "$ORIGIN_ID_PARAMS" ]; then
    echo "Error: No OriginIds found in $IDS_FILE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 3: Fetch engagement data
# ---------------------------------------------------------------------------
echo "Fetching engagement data for period ${PERIOD_START} to ${PERIOD_END}..."

curl -s -f \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Accept: application/json" \
    "https://origin-api.imagineear.com/api/Trial/engagement_data?period_start=${PERIOD_START}&period_end=${PERIOD_END}${ORIGIN_ID_PARAMS}" \
    -o data.json

echo "Data saved to data.json ($(wc -c < data.json) bytes)."
echo "Transfer data.json through the airlock to the VRE."
