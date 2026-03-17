"""
models.py — All data structures for the Church Directory Generator.
No business logic. No I/O. Pure data containers.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# RAW API MODELS  (Planning Center API response shapes)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawAddress:
    street:   str
    city:     str
    state:    str
    zip:      str
    location: str   # 'Home', 'Work', etc.
    primary:  bool


@dataclass
class RawPhone:
    number:   str
    location: str   # 'Mobile', 'Home', 'Work', etc.
    primary:  bool


@dataclass
class RawEmail:
    address:  str
    location: str
    primary:  bool


@dataclass
class RawPerson:
    """Minimally-processed API response. Fields may be None or empty strings."""
    id:             str
    first_name:     str
    last_name:      str
    goes_by_name:   Optional[str]
    status:         str             # 'active', 'inactive'
    membership:     Optional[str]   # 'Member', 'Visitor', etc.
    avatar_url:     Optional[str]   # Planning Center photo URL (may be S3 pre-signed)
    gender:         Optional[str]   # 'Male', 'Female', or None
    household_id:   Optional[str]   # PCO Household record ID (for grouping)
    is_hoh:         bool            # True if this person is the primary contact (head of household)
    addresses:      list[RawAddress]
    phones:         list[RawPhone]
    emails:         list[RawEmail]


# ─────────────────────────────────────────────────────────────────────────────
# DOMAIN MODELS  (clean, validated, normalised)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Address:
    street:       str
    city:         str
    state:        str
    zip:          str
    display:      str           # Pre-formatted for display: "123 Main St, Portland OR 97201"
    group_key:    str           # Normalised key used for household grouping


@dataclass
class Phone:
    number:       str           # Normalised: (503) 555-0101
    location:     str


@dataclass
class Email:
    address:      str
    location:     str


@dataclass
class Person:
    """Validated, normalised member record ready for directory rendering."""
    id:             str
    first_name:     str         # goes_by_name if available, else first_name
    last_name:      str
    sort_key:       str         # "lastname_firstname" normalised for sorting
    address:        Optional[Address]
    phones:         list[Phone]
    emails:         list[Email]
    gender:         Optional[str]   # 'Male', 'Female', or None
    household_id:   Optional[str]   # PCO Household record ID
    is_hoh:         bool            # True = head of household (PCO primary contact)
    photo_path:     Optional[str]   # Local file path after download
    has_photo:      bool            # False = initials placeholder was generated


@dataclass
class DirectoryGroup:
    """
    One or more members who share a last name and address.
    Single-member groups are the common case.
    Multi-member groups represent households.
    """
    sort_key:       str             # For ordering groups on the page
    last_name:      str             # Display last name
    members:        list[Person]    # Sorted by first_name within group
    is_household:   bool            # True = multiple members at same address


@dataclass
class DirectoryPage:
    """A single 5.5x8.5 booklet leaf."""
    page_number:    int
    groups:         list[DirectoryGroup]   # Up to entries_per_page worth of entries


# ─────────────────────────────────────────────────────────────────────────────
# REPORT MODELS  (run diagnostics, logged to run_log)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationWarning:
    person_id:   str
    person_name: str
    field:       str
    message:     str


@dataclass
class ValidationReport:
    total_input:      int
    total_valid:      int
    warnings:         list[ValidationWarning] = field(default_factory=list)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


@dataclass
class GroupingDecision:
    group_key:    str
    member_ids:   list[str]
    reason:       str   # e.g. "Grouped by shared address", "Individual — no address"


@dataclass
class ProcessingReport:
    total_members:    int
    total_groups:     int
    households:       int
    individuals:      int
    no_address:       int
    decisions:        list[GroupingDecision] = field(default_factory=list)


@dataclass
class PhotoResult:
    person_id:    str
    success:      bool
    path:         Optional[str]
    error:        Optional[str]   # None on success


@dataclass
class RunReport:
    """Complete summary of a single directory generation run."""
    timestamp:          str
    member_count:       int
    group_count:        int
    page_count:         int
    photo_successes:    int
    photo_failures:     int
    validation:         ValidationReport
    processing:         ProcessingReport
    output_path:        str
    duration_seconds:   float
    warnings:           list[str] = field(default_factory=list)
    errors:             list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG & AUTH MODELS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Credentials:
    app_id: str
    pat:    str   # Personal Access Token


@dataclass
class AppConfig:
    # Church identity
    church_name:            str
    church_tagline:         str
    church_address:         str
    church_phone:           str
    church_email:           str
    church_service:         str
    directory_year:         str

    # Planning Center
    list_id:                str
    membership_type_label:  str

    # Behaviour
    use_goes_by_name:       bool
    entries_per_page:       int
    photo_pool_size:        int
    fuzzy_match_threshold:  int
    pdf_engine:             str   # 'weasyprint' | 'playwright'

    # Output
    max_run_logs:           int
    keychain_service:       str
    output_filename_format: str


# ─────────────────────────────────────────────────────────────────────────────
# PROGRESS MESSAGES  (thread-safe queue payloads)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProgressMessage:
    stage:    str       # 'auth', 'fetch', 'photos', 'process', 'render', 'pdf', 'done', 'error'
    message:  str       # Human-readable status
    current:  int = 0   # For photo progress: current count
    total:    int = 0   # For photo progress: total count
    error:    Optional[str] = None  # Set on stage='error'
    result:   Optional[RunReport] = None  # Set on stage='done'
