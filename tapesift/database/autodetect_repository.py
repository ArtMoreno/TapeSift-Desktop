"""Persistence for automatic-detection provenance and correction history."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_capture_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class AutodetectRepository:
    """Maps capture dictionaries to the project database.

    Clip identifiers are deliberately not foreign keys. ProjectSession
    replaces the clips table on every write, while capture rows must remain
    after a clip is deleted or restored.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_session(
        self,
        session: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        commit: bool = True,
    ) -> None:
        self.conn.execute(
            """INSERT INTO autodetect_sessions (
                   id, project_id, schema_version, detector_id,
                   detector_version, app_version, source_json,
                   parameters_json, result_json, ui_options_json,
                   provenance_json, runtime_seconds, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session["id"],
                session["project_id"],
                session.get("schema_version", "1.0"),
                session["detector_id"],
                session["detector_version"],
                session["app_version"],
                _json(session["source"]),
                _json(session["parameters"]),
                _json(session["result"]),
                _json(session.get("ui_options", {})),
                _json(session.get("provenance", {})),
                session.get("runtime_seconds"),
                session["created_at"],
            ),
        )
        for candidate in candidates:
            self.conn.execute(
                """INSERT INTO autodetect_candidates (
                       id, session_id, candidate_kind, candidate_index,
                       detector_start_ms, detector_end_ms, created_start_ms,
                       created_end_ms, angle_starts_json, angle_count,
                       needs_review, review_reason, initial_clip_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate["id"],
                    session["id"],
                    candidate["candidate_kind"],
                    candidate["candidate_index"],
                    candidate["detector_start_ms"],
                    candidate["detector_end_ms"],
                    candidate["created_start_ms"],
                    candidate["created_end_ms"],
                    _json(candidate.get("angle_starts_ms", [])),
                    candidate.get("angle_count", 1),
                    int(bool(candidate.get("needs_review", False))),
                    candidate.get("review_reason", ""),
                    candidate["initial_clip_id"],
                    candidate.get("created_at", session["created_at"]),
                ),
            )
        if commit:
            self.conn.commit()

    def record_event(
        self,
        session_id: str,
        *,
        action: str,
        description: str,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        created_at: str | None = None,
        event_id: str | None = None,
        commit: bool = True,
    ) -> str:
        event_id = event_id or new_capture_id("event")
        sequence = self.conn.execute(
            """SELECT COALESCE(MAX(sequence), 0) + 1
               FROM autodetect_events WHERE session_id=?""",
            (session_id,),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO autodetect_events (
                   id, session_id, sequence, action, description, before_json,
                   after_json, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                event_id,
                session_id,
                sequence,
                action,
                description,
                _json(before),
                _json(after),
                created_at or utc_now(),
            ),
        )
        if commit:
            self.conn.commit()
        return event_id

    def create_review_batch(
        self,
        batch: dict[str, Any],
        *,
        commit: bool = True,
    ) -> None:
        self.conn.execute(
            """INSERT INTO autodetect_review_batches (
                   id, session_id, start_ms, end_ms, status,
                   confirmation_json, started_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                batch["id"],
                batch["session_id"],
                batch["start_ms"],
                batch["end_ms"],
                batch.get("status", "active"),
                _json(batch.get("confirmation", {})),
                batch["started_at"],
                batch.get("completed_at", ""),
            ),
        )
        if commit:
            self.conn.commit()

    def complete_review_batch(
        self,
        batch_id: str,
        *,
        confirmation: dict[str, Any],
        completed_at: str | None = None,
        commit: bool = True,
    ) -> None:
        cursor = self.conn.execute(
            """UPDATE autodetect_review_batches
               SET status='completed', confirmation_json=?, completed_at=?
               WHERE id=? AND status='active'""",
            (_json(confirmation), completed_at or utc_now(), batch_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(
                f"Active autodetect review batch not found: {batch_id}")
        if commit:
            self.conn.commit()

    def cancel_review_batch(
        self,
        batch_id: str,
        *,
        confirmation: dict[str, Any],
        cancelled_at: str | None = None,
        commit: bool = True,
    ) -> None:
        cursor = self.conn.execute(
            """UPDATE autodetect_review_batches
               SET status='cancelled', confirmation_json=?, completed_at=?
               WHERE id=? AND status='active'""",
            (_json(confirmation), cancelled_at or utc_now(), batch_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(
                f"Active autodetect review batch not found: {batch_id}")
        if commit:
            self.conn.commit()

    def list_review_batches(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM autodetect_review_batches
               WHERE session_id=? ORDER BY started_at, id""",
            (session_id,),
        ).fetchall()
        return [self._review_batch(row) for row in rows]

    def get_review_batch(
        self,
        batch_id: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM autodetect_review_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        return self._review_batch(row) if row else None

    def get_active_review_batch(
        self,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        if session_id is None:
            row = self.conn.execute(
                """SELECT b.*
                   FROM autodetect_review_batches AS b
                   JOIN autodetect_sessions AS s ON s.id=b.session_id
                   WHERE b.status='active'
                   ORDER BY b.started_at DESC, b.id DESC LIMIT 1"""
            ).fetchone()
        else:
            row = self.conn.execute(
                """SELECT * FROM autodetect_review_batches
                   WHERE session_id=? AND status='active'
                   ORDER BY started_at DESC, id DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
        return self._review_batch(row) if row else None

    def create_recovery(
        self,
        recovery: dict[str, Any],
        *,
        commit: bool = True,
    ) -> None:
        self.conn.execute(
            """INSERT INTO autodetect_recoveries (
                   id, session_id, batch_id, initial_clip_id,
                   created_start_ms, created_end_ms, creation_method,
                   created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                recovery["id"],
                recovery["session_id"],
                recovery["batch_id"],
                recovery["initial_clip_id"],
                recovery["created_start_ms"],
                recovery["created_end_ms"],
                recovery.get("creation_method", "explicit"),
                recovery["created_at"],
            ),
        )
        if commit:
            self.conn.commit()

    def list_recoveries(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM autodetect_recoveries
               WHERE session_id=? ORDER BY created_at, id""",
            (session_id,),
        ).fetchall()
        return [self._recovery(row) for row in rows]

    def upsert_coverage_review(
        self,
        review: dict[str, Any],
        *,
        commit: bool = True,
    ) -> None:
        self.conn.execute(
            """INSERT INTO autodetect_coverage_reviews (
                   session_id, segment_index, status, clip_id, reviewed_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(session_id, segment_index) DO UPDATE SET
                   status=excluded.status,
                   clip_id=excluded.clip_id,
                   reviewed_at=excluded.reviewed_at""",
            (
                review["session_id"],
                int(review["segment_index"]),
                review["status"],
                review.get("clip_id", ""),
                review.get("reviewed_at") or utc_now(),
            ),
        )
        if commit:
            self.conn.commit()

    def delete_coverage_review(
        self,
        session_id: str,
        segment_index: int,
        *,
        commit: bool = True,
    ) -> int:
        cursor = self.conn.execute(
            """DELETE FROM autodetect_coverage_reviews
               WHERE session_id=? AND segment_index=?""",
            (session_id, int(segment_index)),
        )
        if commit:
            self.conn.commit()
        return cursor.rowcount

    def list_coverage_reviews(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM autodetect_coverage_reviews
               WHERE session_id=? ORDER BY segment_index""",
            (session_id,),
        ).fetchall()
        return [{
            "session_id": row["session_id"],
            "segment_index": row["segment_index"],
            "status": row["status"],
            "clip_id": row["clip_id"],
            "reviewed_at": row["reviewed_at"],
        } for row in rows]

    def list_sessions(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM autodetect_sessions
               WHERE project_id=? ORDER BY created_at, id""",
            (project_id,),
        ).fetchall()
        return [self._session(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM autodetect_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        return self._session(row) if row else None

    def list_candidates(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM autodetect_candidates
               WHERE session_id=?
               ORDER BY candidate_kind, candidate_index, id""",
            (session_id,),
        ).fetchall()
        return [self._candidate(row) for row in rows]

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM autodetect_events
               WHERE session_id=? ORDER BY sequence""",
            (session_id,),
        ).fetchall()
        return [self._event(row) for row in rows]

    @staticmethod
    def _session(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "schema_version": row["schema_version"],
            "detector_id": row["detector_id"],
            "detector_version": row["detector_version"],
            "app_version": row["app_version"],
            "source": json.loads(row["source_json"]),
            "parameters": json.loads(row["parameters_json"]),
            "result": json.loads(row["result_json"]),
            "ui_options": json.loads(row["ui_options_json"]),
            "provenance": json.loads(row["provenance_json"]),
            "runtime_seconds": row["runtime_seconds"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _candidate(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "candidate_kind": row["candidate_kind"],
            "candidate_index": row["candidate_index"],
            "detector_start_ms": row["detector_start_ms"],
            "detector_end_ms": row["detector_end_ms"],
            "created_start_ms": row["created_start_ms"],
            "created_end_ms": row["created_end_ms"],
            "angle_starts_ms": json.loads(row["angle_starts_json"]),
            "angle_count": row["angle_count"],
            "needs_review": bool(row["needs_review"]),
            "review_reason": row["review_reason"],
            "initial_clip_id": row["initial_clip_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "sequence": row["sequence"],
            "action": row["action"],
            "description": row["description"],
            "before": json.loads(row["before_json"]),
            "after": json.loads(row["after_json"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _review_batch(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "status": row["status"],
            "confirmation": json.loads(row["confirmation_json"]),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    @staticmethod
    def _recovery(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "batch_id": row["batch_id"],
            "initial_clip_id": row["initial_clip_id"],
            "created_start_ms": row["created_start_ms"],
            "created_end_ms": row["created_end_ms"],
            "creation_method": row["creation_method"],
            "created_at": row["created_at"],
        }
