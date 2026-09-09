"""Row deduplication and incremental diffs.

Deduplication uses the target's declared row_key. With no key declared every
row is kept: collapsing rows on a key the operator did not choose loses data
silently, which is the worst way to lose it.

An incremental run compares this run's rows against the stored snapshot of the
same target and reports added, changed and removed instead of the whole list.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapequeue.core.targets import TargetSpec
from scrapequeue.db.tables import TargetSnapshot
from scrapequeue.workers.extract import row_identity

Row = dict[str, Any]


def row_hash(row: Row) -> str:
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class DedupeResult:
    rows: list[Row] = field(default_factory=list)
    duplicates: int = 0


def dedupe(rows: list[Row], spec: TargetSpec) -> DedupeResult:
    if not spec.extraction.row_key:
        return DedupeResult(rows=list(rows))
    seen: set[str] = set()
    kept: list[Row] = []
    duplicates = 0
    for row in rows:
        ident = row_identity(row, spec)
        if ident in seen:
            duplicates += 1
            continue
        seen.add(ident)
        kept.append(row)
    return DedupeResult(rows=kept, duplicates=duplicates)


@dataclass
class Diff:
    added: list[Row] = field(default_factory=list)
    changed: list[Row] = field(default_factory=list)
    removed_keys: list[str] = field(default_factory=list)

    @property
    def rows(self) -> list[Row]:
        return self.added + self.changed


def diff_against_snapshot(session: Session, spec: TargetSpec, rows: list[Row]) -> Diff:
    stored = {
        row_key: content_hash
        for row_key, content_hash in session.execute(
            select(TargetSnapshot.row_key, TargetSnapshot.content_hash).where(
                TargetSnapshot.target == spec.name
            )
        ).all()
    }
    result = Diff()
    seen: set[str] = set()
    for row in rows:
        key = row_identity(row, spec)
        seen.add(key)
        digest = row_hash(row)
        previous = stored.get(key)
        if previous is None:
            result.added.append(row)
        elif previous != digest:
            result.changed.append(row)
    result.removed_keys = sorted(set(stored) - seen)
    return result


def store_snapshot(session: Session, spec: TargetSpec, rows: list[Row], job_id: Any) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not rows:
        return
    values = [
        {
            "target": spec.name,
            "row_key": row_identity(row, spec),
            "content_hash": row_hash(row),
            "job_id": job_id,
        }
        for row in rows
    ]
    stmt = pg_insert(TargetSnapshot).values(values)
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_target_snapshots_row",
            set_={"content_hash": stmt.excluded.content_hash, "job_id": stmt.excluded.job_id},
        )
    )
