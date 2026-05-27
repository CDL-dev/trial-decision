"""Submission persistence and admin override merge helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SUBMISSION_BUSINESS_FIELDS = {
    "loan",
    "engineers_change",
    "engineer_salary",
    "quality_investment",
    "volume",
    "city_sales",
}


def merge_submission_with_override(submission: dict, override: dict) -> dict:
    """Merge player submission with admin override, keeping admin metadata separate.

    Returns {"business": ..., "admin_meta": ...}.
    Only whitelisted business fields from the override are merged into the
    business payload. Fields like bonus_penalty are routed to admin_meta.
    """
    business = dict(submission or {})
    admin_meta: dict[str, object] = {}
    for key, value in (override or {}).items():
        if key in SUBMISSION_BUSINESS_FIELDS:
            business[key] = value
        else:
            admin_meta[key] = value
    return {"business": business, "admin_meta": admin_meta}


def upsert_submission(
    db_path: Path,
    match_id: int,
    round_index: int,
    player_id: int,
    payload: dict,
    is_final: bool = True,
) -> None:
    """Save or overwrite a player's submission for a given round.

    Uses INSERT ON CONFLICT to ensure exactly one record per
    (match_id, round_index, player_id).
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO round_submissions
                (match_id, round_index, player_id, submitted_at, is_final, payload_json)
            VALUES (?, ?, ?, datetime('now'), ?, ?)
            ON CONFLICT(match_id, round_index, player_id) DO UPDATE SET
                submitted_at = datetime('now'),
                is_final = excluded.is_final,
                payload_json = excluded.payload_json
            """,
            (match_id, round_index, player_id, int(is_final), json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_override(
    db_path: Path,
    match_id: int,
    round_index: int,
    player_id: int,
    override: dict,
    bonus_penalty: float = 0.0,
) -> None:
    """Save or overwrite an admin override for a given player round."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO round_overrides
                (match_id, round_index, player_id, override_json, bonus_penalty, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(match_id, round_index, player_id) DO UPDATE SET
                override_json = excluded.override_json,
                bonus_penalty = excluded.bonus_penalty,
                updated_at = datetime('now')
            """,
            (match_id, round_index, player_id, json.dumps(override), bonus_penalty),
        )
        conn.commit()
    finally:
        conn.close()
