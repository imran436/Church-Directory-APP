"""
validator.py — Data normalisation and validation.

Pure functions only — no I/O, no network, no side effects.
Transforms RawPerson[] into Person[] with a ValidationReport.

Normalisation applied:
- HTML entities stripped from name fields
- Phone numbers normalised to (XXX) XXX-XXXX
- Empty strings treated as None
- Primary address/phone/email selected
- goes_by_name applied as display first name
- Address display string and group key computed
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from typing import Optional

from models import (
    RawPerson, Person, Address, Phone, Email,
    ValidationReport, ValidationWarning,
)

logger = logging.getLogger(__name__)


# ── Phone normalisation ───────────────────────────────────────────────────────

_PHONE_DIGITS = re.compile(r"\D")

def _normalise_phone(raw: str) -> Optional[str]:
    """
    Normalise a phone number to (XXX) XXX-XXXX.
    Returns None if the number cannot be normalised to 10 digits.
    """
    if not raw:
        return None
    digits = _PHONE_DIGITS.sub("", raw)
    # Strip leading country code '1' if 11 digits
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    # Can't normalise — return stripped version
    logger.debug("Could not normalise phone %r to 10 digits", raw)
    return raw.strip() or None


# ── Email validation ──────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _valid_email(address: str) -> bool:
    return bool(_EMAIL_RE.match(address.strip()))


# ── Name cleaning ─────────────────────────────────────────────────────────────

def _clean_name(raw: str) -> str:
    """Strip HTML entities and normalise whitespace."""
    if not raw:
        return ""
    # Unescape HTML entities: &amp; → &, &#39; → ', etc.
    unescaped = html.unescape(raw)
    # Normalise unicode (NFC — composed form)
    normalised = unicodedata.normalize("NFC", unescaped)
    # Collapse whitespace
    return " ".join(normalised.split())


# ── Address normalisation ─────────────────────────────────────────────────────

# Common abbreviation expansions for grouping key generation only
# (display address is left as-is from Planning Center)
_ABBR = {
    r"\bSt\b":   "Street",
    r"\bAve\b":  "Avenue",
    r"\bBlvd\b": "Boulevard",
    r"\bDr\b":   "Drive",
    r"\bRd\b":   "Road",
    r"\bLn\b":   "Lane",
    r"\bCt\b":   "Court",
    r"\bPl\b":   "Place",
    r"\bPkwy\b": "Parkway",
    r"\bHwy\b":  "Highway",
    r"\bFwy\b":  "Freeway",
}

def _address_group_key(street: str, zip_: str) -> str:
    """
    Produce a normalised string used as the household grouping key.
    Expands abbreviations, strips punctuation, lowercases.
    """
    key = street.lower()
    for pattern, expansion in _ABBR.items():
        key = re.sub(pattern, expansion.lower(), key, flags=re.IGNORECASE)
    # Strip punctuation except digits and letters
    key = re.sub(r"[^a-z0-9\s]", "", key)
    key = " ".join(key.split())   # Collapse whitespace
    return f"{key}_{zip_.strip()}"


def _build_address(raw_addresses: list) -> Optional[Address]:
    """Select the primary (or first) address and build an Address object."""
    if not raw_addresses:
        return None

    # Prefer primary; fall back to first
    addr = next((a for a in raw_addresses if a.primary), raw_addresses[0])

    # Skip if all fields are empty
    if not any([addr.street, addr.city, addr.state, addr.zip]):
        return None

    parts = []
    if addr.street: parts.append(addr.street)
    city_state_zip = " ".join(filter(None, [addr.city, addr.state, addr.zip]))
    if city_state_zip: parts.append(city_state_zip)
    display = ", ".join(parts)

    group_key = _address_group_key(addr.street or "", addr.zip or "")

    return Address(
        street    = addr.street,
        city      = addr.city,
        state     = addr.state,
        zip       = addr.zip,
        display   = display,
        group_key = group_key,
    )


# ── Sort key ──────────────────────────────────────────────────────────────────

def _sort_key(last: str, first: str) -> str:
    """Produce a normalised sort key: 'lastname_firstname' lowercased."""
    def norm(s: str) -> str:
        # Normalise unicode, lowercase, strip punctuation for sorting
        s = unicodedata.normalize("NFD", s.lower())
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return s.strip()
    return f"{norm(last)}_{norm(first)}"


# ── Main validation function ──────────────────────────────────────────────────

def validate_and_normalise(
    raw_people: list[RawPerson],
    use_goes_by_name: bool = True,
) -> tuple[list[Person], ValidationReport]:
    """
    Validate and normalise RawPerson records into Person records.

    Args:
        raw_people:        Raw API data
        use_goes_by_name:  If True, use goes_by_name as display first name

    Returns:
        (people, report) — people is a list of normalised Person records.
    """
    people   : list[Person]             = []
    warnings : list[ValidationWarning]  = []

    def warn(person_id: str, person_name: str, field: str, msg: str):
        warnings.append(ValidationWarning(person_id=person_id, person_name=person_name, field=field, message=msg))
        logger.debug("Validation warning [%s] %s: %s", person_id, field, msg)

    for raw in raw_people:
        pid = raw.id

        # ── Names ─────────────────────────────────────────────────────────────
        first = _clean_name(raw.first_name)
        last  = _clean_name(raw.last_name)

        if not last:
            warn(pid, f"{first or '?'}", "last_name", "Empty last name — will sort under '#'")
            last = "#"

        # Apply goes_by_name preference
        display_first = first
        if use_goes_by_name and raw.goes_by_name:
            goes_by = _clean_name(raw.goes_by_name)
            if goes_by and goes_by != first:
                display_first = goes_by

        # ── Address ───────────────────────────────────────────────────────────
        address = _build_address(raw.addresses)
        pname = f"{first or '?'} {last or '?'}".strip()
        if not address:
            warn(pid, pname, "address", "No address on file — will be listed individually")

        # ── Phones ────────────────────────────────────────────────────────────
        phones: list[Phone] = []
        raw_phones = raw.phones or []
        # Primary first
        raw_phones_sorted = sorted(raw_phones, key=lambda p: (not p.primary, p.location))
        for rp in raw_phones_sorted:
            normalised = _normalise_phone(rp.number)
            if normalised:
                phones.append(Phone(number=normalised, location=rp.location or ""))
            else:
                warn(pid, pname, "phone", f"Could not normalise phone number: {rp.number!r}")

        # ── Emails ────────────────────────────────────────────────────────────
        emails: list[Email] = []
        raw_emails = raw.emails or []
        raw_emails_sorted = sorted(raw_emails, key=lambda e: (not e.primary, e.location))
        for re_ in raw_emails_sorted:
            addr = re_.address.strip().lower()
            if not addr:
                continue
            if not _valid_email(addr):
                warn(pid, pname, "email", f"Possibly malformed email: {addr!r}")
            emails.append(Email(address=addr, location=re_.location or ""))

        people.append(Person(
            id          = pid,
            first_name  = display_first,
            last_name   = last,
            sort_key    = _sort_key(last, display_first),
            address     = address,
            phones      = phones,
            emails      = emails,
            gender      = raw.gender,
            household_id = raw.household_id,
            is_hoh      = raw.is_hoh,
            photo_path  = raw.avatar_url,   # Local path set by pc_client, or None
            has_photo   = False,            # Set by photo_handler after placeholder gen
        ))

    report = ValidationReport(
        total_input = len(raw_people),
        total_valid = len(people),
        warnings    = warnings,
    )

    return people, report
