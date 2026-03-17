"""
processor.py — Sort, group, and paginate members for directory rendering.

Pure functions only — no I/O, no network, no side effects.

Grouping strategy (two-pass):
  Pass 1 — PCO household_id: group members who share the same Planning Center
            household record. The primary_contact (is_hoh=True) is the head.
  Pass 2 — Address merge: any single-person group whose address exactly matches
            a multi-person group's HOH address is absorbed into that group.
            This handles adult children who have their own PCO household but
            still live at home (e.g. Caden/Jack Best under Justin & Amy).

Sort order within each group:
  1. Head of household (is_hoh=True) first
  2. Remaining Males alphabetically
  3. Females alphabetically
"""

from __future__ import annotations

import logging
from collections import defaultdict

from models import (
    Person, DirectoryGroup, DirectoryPage,
    ProcessingReport, GroupingDecision,
)

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 92  # kept for API compat; address merge uses exact match


def _addr_key(person: Person) -> str | None:
    """Normalised address string for exact matching, or None."""
    if person.address is None:
        return None
    a = person.address
    street = (a.street or "").strip().lower()
    zip_   = (a.zip          or "").strip()
    return f"{street} {zip_}" if street else None


def _member_sort_key(p: Person):
    """HOH first, then spouse (original PCO household female), then males alpha, then females alpha."""
    hoh_rank    = 0 if p.is_hoh else 1
    # is_spouse: female who was in the same PCO household as the HOH (not address-merged)
    spouse_rank = 1 if getattr(p, '_is_spouse', False) else 2
    gender_rank = 0 if (p.gender or "").lower() == "male" else 1
    return (hoh_rank, spouse_rank, gender_rank, p.first_name.lower())


def _group_members(
    people: list[Person],
    fuzzy_threshold: int = DEFAULT_THRESHOLD,
) -> tuple[list[DirectoryGroup], ProcessingReport]:
    decisions: list[GroupingDecision] = []

    # ── Pass 1: PCO household_id grouping ─────────────────────────────────────
    hh_buckets: dict[str, list[Person]] = defaultdict(list)
    no_hh: list[Person] = []

    for person in people:
        if person.household_id:
            hh_buckets[person.household_id].append(person)
        else:
            no_hh.append(person)

    # Build initial groups from PCO households
    # Mark non-HOH members of multi-person groups as spouses so they sort
    # immediately after the HOH (before address-merged kids)
    groups: list[DirectoryGroup] = []
    for hh_id, members in hh_buckets.items():
        # Tag non-HOH members of real PCO households as spouses
        for m in members:
            if not m.is_hoh:
                object.__setattr__(m, '_is_spouse', True) if hasattr(m, '__dataclass_fields__') else setattr(m, '_is_spouse', True)
        members_sorted = sorted(members, key=_member_sort_key)
        groups.append(DirectoryGroup(
            sort_key     = members_sorted[0].sort_key,
            last_name    = members_sorted[0].last_name,
            members      = members_sorted,
            is_household = len(members_sorted) > 1,
        ))
        decisions.append(GroupingDecision(
            group_key  = f"hh_{hh_id}",
            member_ids = [p.id for p in members_sorted],
            reason     = f"PCO Household {hh_id}",
        ))

    # People with no household_id become solo groups
    for person in no_hh:
        groups.append(DirectoryGroup(
            sort_key     = person.sort_key,
            last_name    = person.last_name,
            members      = [person],
            is_household = False,
        ))
        decisions.append(GroupingDecision(
            group_key  = f"solo_{person.id}",
            member_ids = [person.id],
            reason     = "No PCO household ID",
        ))

    # ── Pass 2: Address merge ──────────────────────────────────────────────────
    # Group all PC households by (last_name, street+zip).
    # Within each address cluster, pick the ANCHOR = the group whose HOH was
    # set as primary_contact by PC (i.e. has is_hoh=True and was the original
    # PC primary contact — proxy: largest group, or oldest PC household id as
    # tiebreak).  Every other group at that same address+lastname gets absorbed
    # into the anchor.  Non-HOH members keep their sort position; all absorbed
    # members have is_hoh cleared so they sort after the anchor couple.
    #
    # This handles:
    #   Ben & Anna Cools  → under Dave & Teri Cools (same address)
    #   Elena Sorensen    → under Ryan & Stephanie Sorensen
    #   Abigail/Ruth Winslow → under Alan & Gini Winslow
    #   Emily/Josiah/Lucy Winslow → under Gabe & Mandy Winslow
    #   Caden/Jack/Natalie Best → under Justin & Amy Best
    #   Judah/Caleb/Samuel Zrust → under Matt & Amber Zrust

    # Build address clusters: (last_name_lower, street|zip) → [group_index]
    addr_clusters: dict[tuple, list[int]] = defaultdict(list)
    for i, g in enumerate(groups):
        rep = next((m for m in g.members if m.is_hoh), g.members[0])
        ak = _addr_key(rep)
        if ak:
            cluster_key = (rep.last_name.strip().lower(), ak)
            addr_clusters[cluster_key].append(i)

    absorbed: set[int] = set()

    for cluster_key, indices in addr_clusters.items():
        if len(indices) < 2:
            continue

        # Anchor = largest group; tiebreak by smallest household_id (oldest in PC)
        def _anchor_rank(idx):
            g = groups[idx]
            hoh = next((m for m in g.members if m.is_hoh), g.members[0])
            hh_id_num = int(hoh.household_id) if (hoh.household_id or '').isdigit() else 999999999
            return (-len(g.members), hh_id_num)

        anchor_idx = min(indices, key=_anchor_rank)
        anchor = groups[anchor_idx]

        # Tag anchor members with sub-group 0
        for m in anchor.members:
            object.__setattr__(m, '_subgroup', 0)

        sub = 1
        for i in sorted(indices, key=_anchor_rank):
            if i == anchor_idx:
                continue
            g = groups[i]
            # Sort absorbed group internally (HOH of absorbed group first, then spouse)
            absorbed_sorted = sorted(g.members, key=_member_sort_key)
            for m in absorbed_sorted:
                object.__setattr__(m, 'is_hoh', False)
                object.__setattr__(m, '_subgroup', sub)
            anchor.members.extend(absorbed_sorted)
            anchor.is_household = True
            absorbed.add(i)
            sub += 1
            decisions.append(GroupingDecision(
                group_key  = f"addr_merge_{'_'.join(m.id for m in g.members)}",
                member_ids = [m.id for m in g.members],
                reason     = f"Address merge → anchor {anchor.sort_key}",
            ))
            logger.debug(
                "Address merge: %s absorbed into %s household",
                [f"{m.first_name} {m.last_name}" for m in g.members],
                anchor.members[0].last_name,
            )

        # Sort: anchor couple first (subgroup 0, HOH then others), then each
        # absorbed sub-group in order, preserving internal male-first ordering
        def _merged_sort_key(p: Person):
            sg  = getattr(p, '_subgroup', 0)
            hoh = 0 if p.is_hoh else 1
            gen = 0 if (p.gender or "").lower() == "male" else 1
            return (sg, hoh, gen, p.first_name.lower())

        anchor.members = sorted(anchor.members, key=_merged_sort_key)
        anchor.sort_key = anchor.members[0].sort_key

    # Remove absorbed groups
    groups = [g for i, g in enumerate(groups) if i not in absorbed]

    # ── Final sort ─────────────────────────────────────────────────────────────
    groups_sorted = sorted(groups, key=lambda g: g.sort_key)

    households = sum(1 for g in groups_sorted if g.is_household)
    ind_count  = sum(1 for g in groups_sorted if not g.is_household)
    no_addr    = sum(1 for g in groups_sorted
                     if not g.is_household and _addr_key(g.members[0]) is None)

    report = ProcessingReport(
        total_members = len(people),
        total_groups  = len(groups_sorted),
        households    = households,
        individuals   = ind_count,
        no_address    = no_addr,
        decisions     = decisions,
    )
    return groups_sorted, report


def _paginate(
    groups: list[DirectoryGroup],
    entries_per_page: int = 4,
) -> list[DirectoryPage]:
    """
    Distribute groups across directory pages, filling every page to exactly
    entries_per_page entries.

    Household members may split across pages — the gold border on each
    member card preserves the household visual connection regardless.
    Only the last page may have fewer than entries_per_page entries.
    """
    slots = []
    for group in groups:
        for i in range(len(group.members)):
            slots.append((group, i))

    pages = []
    page_num = 1

    for offset in range(0, len(slots), entries_per_page):
        chunk = slots[offset: offset + entries_per_page]

        page_groups = []
        cur_group   = None
        cur_members = []

        for (group, member_idx) in chunk:
            if cur_group is None or group is not cur_group:
                if cur_group is not None:
                    page_groups.append(DirectoryGroup(
                        sort_key     = cur_group.sort_key,
                        last_name    = cur_group.last_name,
                        members      = list(cur_members),
                        is_household = cur_group.is_household,
                    ))
                cur_group   = group
                cur_members = []
            cur_members.append(group.members[member_idx])

        if cur_group is not None:
            page_groups.append(DirectoryGroup(
                sort_key     = cur_group.sort_key,
                last_name    = cur_group.last_name,
                members      = list(cur_members),
                is_household = cur_group.is_household,
            ))

        pages.append(DirectoryPage(page_number=page_num, groups=page_groups))
        page_num += 1

    return pages


def process(
    people: list[Person],
    entries_per_page: int = 4,
    fuzzy_threshold:  int = DEFAULT_THRESHOLD,
) -> tuple[list[DirectoryPage], list[DirectoryGroup], ProcessingReport]:
    groups, report = _group_members(people, fuzzy_threshold)
    pages           = _paginate(groups, entries_per_page)

    logger.info(
        "Processing complete: %d members → %d groups (%d households, %d individuals) → %d pages",
        report.total_members, report.total_groups,
        report.households, report.individuals, len(pages),
    )

    return pages, groups, report
