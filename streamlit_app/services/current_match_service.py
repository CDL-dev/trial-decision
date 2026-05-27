"""Single-match state queries — only one active match is supported."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def get_current_match(db_path: Path) -> dict | None:
    """Return the current match (the most recent non-ended, or just ended)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM matches WHERE status != 'ended' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM matches ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def has_active_match(db_path: Path) -> bool:
    """True if there is a non-ended match."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM matches WHERE status != 'ended' LIMIT 1"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_match_phase(db_path: Path) -> str:
    """Return the current match phase: empty, setup, running, or ended."""
    match = get_current_match(db_path)
    if match is None:
        return "empty"
    status = match["status"]
    if status == "setup":
        return "setup"
    if status == "running":
        return "running"
    if status == "ended":
        return "ended"
    return "empty"
