"""Trial API client with automatic OAuth2 token refresh.

Credentials are read from environment variables:
  TRIAL_CLIENT_ID      — M2M client ID (provided by the back-end dev)
  TRIAL_CLIENT_SECRET  — M2M client secret (provided by the back-end dev)

Tokens expire after 300 seconds; the client re-fetches automatically.

Usage:
    from trial.client import TrialClient
    client = TrialClient.from_env()
    participants = client.fetch(period_start, period_end)
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from .models import ParticipantEngagement

TOKEN_URL = (
    "https://auth.imagineear.com/realms/OriginWOB/protocol/openid-connect/token"
)
API_URL = "https://origin-api.imagineear.com/api/Trial/engagement_data"

ORIGIN_ID_RE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")


def load_ids(path: str) -> list[str]:
    """Load OriginIds from a file (one per line, # comments and blank lines ignored).

    Raises FileNotFoundError with a helpful message if the file is absent —
    the API no longer accepts requests without an explicit ID list.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"ID list file not found: {path}\n"
            "Create it with one OriginId per line (format XXXX-XXXX).\n"
            "See ids/README.md for details."
        )
    ids = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not ORIGIN_ID_RE.match(line):
                raise ValueError(
                    f"Invalid OriginId {line!r} in {path} — expected XXXX-XXXX "
                    "(uppercase alphanumeric, 4 chars each side)"
                )
            ids.append(line)
    if not ids:
        raise ValueError(f"ID list file {path} contains no valid OriginIds")
    return ids

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Default ID list files, one per user group (user_type value).
# Files live in ids/ at the repo root and are gitignored — obtain from the study team.
_IDS_DIR = os.path.join(_REPO_ROOT, "ids")
DEFAULT_IDS_FILES = {
    0: os.path.join(_IDS_DIR, "ids_0.txt"),  # trial participants
    1: os.path.join(_IDS_DIR, "ids_1.txt"),  # test accounts
    2: os.path.join(_IDS_DIR, "ids_2.txt"),  # usability trial
}
# The file the CLI tools load by default when no --ids-file is given.
# Points at the usability trial during this phase; update as the trial progresses.
DEFAULT_IDS_FILE = DEFAULT_IDS_FILES[2]


def _load_dotenv() -> None:
    """Load a .env file from the repo root into os.environ (no-op if absent)."""
    path = os.path.join(_REPO_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())



class TrialAPIError(Exception):
    """Raised when the Trial API returns an error response."""
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class TrialClient:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    @classmethod
    def from_env(cls) -> "TrialClient":
        """Construct from TRIAL_CLIENT_ID and TRIAL_CLIENT_SECRET env vars.

        If either variable is missing, looks for a .env file in the repo root
        and loads it before checking again.
        """
        _load_dotenv()
        client_id = os.environ.get("TRIAL_CLIENT_ID", "")
        client_secret = os.environ.get("TRIAL_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise EnvironmentError(
                "TRIAL_CLIENT_ID and TRIAL_CLIENT_SECRET must be set. "
                "Obtain these from the back-end dev."
            )
        return cls(client_id, client_secret)

    def _get_token(self) -> str:
        """Return a valid access token, refreshing if expired or near expiry."""
        # Refresh 30 seconds before expiry to avoid races
        if self._token and time.time() < self._token_expiry - 30:
            return self._token

        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "openid profile",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            raise TrialAPIError(resp.status_code, f"Token request failed: {resp.text}")

        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + payload.get("expires_in", 300)
        return self._token

    def fetch(
        self,
        period_start: datetime,
        period_end: datetime,
        origin_ids: Optional[list[str]] = None,
    ) -> list[ParticipantEngagement]:
        """Fetch engagement data for the given period.

        Args:
            period_start: Start of the reporting window (timezone-aware).
            period_end:   End of the reporting window (timezone-aware).
            origin_ids:   Optional list of participant IDs (format XXXX-XXXX).
                          If omitted, all participants are returned.

        Returns:
            List of ParticipantEngagement objects, one per participant.
        """
        if origin_ids:
            for oid in origin_ids:
                if not ORIGIN_ID_RE.match(oid):
                    raise ValueError(
                        f"Invalid originId {oid!r} — must be XXXX-XXXX "
                        "(uppercase alphanumeric, 4 chars each side)"
                    )

        params: list[tuple[str, str]] = [
            ("period_start", _fmt_dt(period_start)),
            ("period_end", _fmt_dt(period_end)),
        ]
        for oid in (origin_ids or []):
            params.append(("originIds", oid))

        token = self._get_token()
        resp = requests.get(
            API_URL,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=30,
        )

        if resp.status_code == 400:
            raise TrialAPIError(400, f"Bad request: {resp.text}")
        if resp.status_code == 401:
            raise TrialAPIError(401, "Unauthorized — token invalid or expired")
        if resp.status_code == 403:
            raise TrialAPIError(403, "Forbidden — token lacks trial_api_user role")
        if resp.status_code != 200:
            raise TrialAPIError(resp.status_code, resp.text)

        return [ParticipantEngagement.from_dict(p) for p in resp.json()]


def _fmt_dt(dt: datetime) -> str:
    """Format a datetime as ISO 8601 UTC string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
