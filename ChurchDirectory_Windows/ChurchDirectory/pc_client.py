"""
pc_client.py — Planning Center People API client.

Single responsibility: talk to Planning Center and return RawPerson records.

Key design decisions:
- Single paginated call with sideloaded includes (NOT N+1 per-person calls)
- Photos downloaded immediately per person to avoid S3 URL expiry
- Exponential backoff on HTTP 429
- Specific exception types for each failure mode
- API version header pinned
"""

from __future__ import annotations

import base64
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models import Credentials, RawPerson, RawAddress, RawPhone, RawEmail
from errors import (
    CredentialsInvalidError, ListNotFoundError,
    RateLimitError, NetworkError, ZeroMembersError
)

logger = logging.getLogger(__name__)

BASE_URL   = "https://api.planningcenteronline.com/people/v2"
API_VER    = "2023-03-01"
PAGE_SIZE  = 100
MAX_RETRY  = 3
BACKOFF    = [1, 2, 4]   # seconds


def _make_session(credentials: Credentials) -> requests.Session:
    """Build a requests Session with Basic Auth and retry handling."""
    session = requests.Session()

    token = base64.b64encode(
        f"{credentials.app_id}:{credentials.pat}".encode()
    ).decode()

    session.headers.update({
        "Authorization":    f"Basic {token}",
        "Content-Type":     "application/json",
        "X-PCO-API-Version": API_VER,
    })

    # Retry on connection errors / 500s (NOT 429 — we handle that ourselves)
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


def _get_with_backoff(session: requests.Session, url: str, params: dict) -> dict:
    """
    GET request with exponential backoff on HTTP 429.
    Raises specific DirectoryError subtypes on failure.
    """
    for attempt, wait in enumerate(BACKOFF + [None]):
        try:
            resp = session.get(url, params=params, timeout=30)
        except requests.ConnectionError as e:
            raise NetworkError(str(e)) from e
        except requests.Timeout:
            raise NetworkError("Request timed out after 30 seconds.") from None

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 401:
            raise CredentialsInvalidError()

        if resp.status_code == 404:
            raise ListNotFoundError(url)

        if resp.status_code == 429:
            if wait is None:
                raise RateLimitError()
            logger.warning("Rate limited — retrying in %ds (attempt %d/%d)", wait, attempt + 1, MAX_RETRY)
            time.sleep(wait)
            continue

        # Unexpected status
        raise NetworkError(f"Unexpected HTTP {resp.status_code} from {url}")

    raise RateLimitError()  # Should not reach here


# ── Response parsing helpers ──────────────────────────────────────────────────

def _parse_addresses(included: list[dict], person_id: str) -> list[RawAddress]:
    addrs = []
    for item in included:
        if item.get("type") != "Address":
            continue
        rel = item.get("relationships", {}).get("person", {}).get("data", {})
        if rel.get("id") != person_id:
            continue
        a = item.get("attributes", {})
        # Real PCO API uses street_line_1 / street_line_2 (not "street")
        line1  = (a.get("street_line_1") or a.get("street") or "").strip()
        line2  = (a.get("street_line_2") or "").strip()
        street = f"{line1} {line2}".strip() if line2 else line1
        addrs.append(RawAddress(
            street   = street,
            city     = (a.get("city")   or "").strip(),
            state    = (a.get("state")  or "").strip(),
            zip      = (a.get("zip")    or "").strip(),
            location = (a.get("location") or "Home").strip(),
            primary  = bool(a.get("primary", False)),
        ))
    return addrs


def _parse_phones(included: list[dict], person_id: str) -> list[RawPhone]:
    phones = []
    for item in included:
        if item.get("type") != "PhoneNumber":
            continue
        rel = item.get("relationships", {}).get("person", {}).get("data", {})
        if rel.get("id") != person_id:
            continue
        a = item.get("attributes", {})
        phones.append(RawPhone(
            number   = (a.get("number") or "").strip(),
            location = (a.get("location") or "").strip(),
            primary  = bool(a.get("primary", False)),
        ))
    return phones


def _parse_emails(included: list[dict], person_id: str) -> list[RawEmail]:
    emails = []
    for item in included:
        if item.get("type") != "Email":
            continue
        rel = item.get("relationships", {}).get("person", {}).get("data", {})
        if rel.get("id") != person_id:
            continue
        a = item.get("attributes", {})
        # Skip blocked emails — they cannot receive mail and should not be printed
        if a.get("blocked", False):
            logger.debug("Skipping blocked email for person %s", person_id)
            continue
        emails.append(RawEmail(
            address  = (a.get("address") or "").strip(),
            location = (a.get("location") or "").strip(),
            primary  = bool(a.get("primary", False)),
        ))
    return emails


def _parse_person(data: dict, included: list[dict]) -> RawPerson:
    """Parse a single person record from the API response."""
    pid   = data["id"]
    attrs = data.get("attributes", {})

    # Extract household relationship — PCO returns the household record in `included`
    # and links it via relationships.households.data[].id
    household_id = None
    is_hoh       = False
    hh_rels = (data.get("relationships", {})
                   .get("households", {})
                   .get("data", []))
    if hh_rels:
        household_id = hh_rels[0].get("id")
        # Find the matching household record in included to get primary_contact_id
        for item in included:
            if item.get("type") == "Household" and item.get("id") == household_id:
                primary_id = (item.get("attributes", {})
                                  .get("primary_contact_id") or "")
                is_hoh = (str(primary_id) == str(pid))
                break

    return RawPerson(
        id           = pid,
        first_name   = (attrs.get("first_name")   or "").strip(),
        last_name    = (attrs.get("last_name")    or "").strip(),
        goes_by_name = (attrs.get("given_name") or attrs.get("goes_by_name") or "").strip() or None,
        status       = (attrs.get("status")       or "").strip(),
        membership   = (attrs.get("membership")   or "").strip() or None,
        avatar_url   = attrs.get("avatar") or None,
        gender       = (attrs.get("gender") or "").strip() or None,
        household_id = household_id,
        is_hoh       = is_hoh,
        addresses    = _parse_addresses(included, pid),
        phones       = _parse_phones(included, pid),
        emails       = _parse_emails(included, pid),
    )


# ── Photo download (called per-person during pagination) ──────────────────────

def _download_photo(session: requests.Session, url: str, dest_path: str) -> bool:
    """
    Download a photo from Planning Center to dest_path.
    Returns True on success. Called immediately per person to avoid S3 URL expiry.
    """
    if not url:
        return False
    try:
        resp = session.get(url, timeout=20, stream=True)
        if resp.status_code == 200:
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        logger.warning("Photo download failed: HTTP %d for %s", resp.status_code, url)
        return False
    except Exception as e:
        logger.warning("Photo download exception: %s", e)
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def validate_credentials(credentials: Credentials) -> bool:
    """
    Make a lightweight call to /people/v2/me to verify credentials.
    Returns True if valid. Raises CredentialsInvalidError if rejected.
    """
    session = _make_session(credentials)
    try:
        resp = session.get(f"{BASE_URL}/me", timeout=10)
        if resp.status_code == 200:
            return True
        if resp.status_code == 401:
            raise CredentialsInvalidError()
        raise NetworkError(f"Unexpected HTTP {resp.status_code} during credential validation")
    except requests.ConnectionError as e:
        raise NetworkError(str(e)) from e


def fetch_lists(credentials: Credentials) -> list[dict]:
    """
    Return all available Planning Center lists as [{"id": ..., "name": ...}].
    Used during setup to populate the list picker dropdown.
    """
    session = _make_session(credentials)
    lists   = []
    url     = f"{BASE_URL}/lists"

    while url:
        data = _get_with_backoff(session, url, {"per_page": PAGE_SIZE})
        for item in data.get("data", []):
            lists.append({
                "id":   item["id"],
                "name": item.get("attributes", {}).get("name") or f"List {item['id']}",
            })
        url = data.get("links", {}).get("next")

    return sorted(lists, key=lambda x: (x["name"] or "").lower())


def fetch_members(
    credentials: Credentials,
    list_id:     str,
) -> list[RawPerson]:
    """
    Fetch all members from the given Planning Center list.
    avatar_url is left as the original Planning Center URL — no downloading.

    Raises:
        ListNotFoundError, CredentialsInvalidError, RateLimitError, NetworkError,
        ZeroMembersError
    """
    session  = _make_session(credentials)
    people   = []
    url      = f"{BASE_URL}/lists/{list_id}/people"
    params   = {
        "include":   "households,addresses,phone_numbers,emails,marital_status",
        "per_page":  PAGE_SIZE,
    }

    logger.info("Fetching members from list %s …", list_id)

    while url:
        data     = _get_with_backoff(session, url, params)
        included = data.get("included", [])
        for item in data.get("data", []):
            people.append(_parse_person(item, included))
        url    = data.get("links", {}).get("next")
        params = {}

    if not people:
        raise ZeroMembersError(list_id)

    logger.info("Fetched %d members", len(people))
    return people

