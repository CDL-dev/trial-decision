"""SQLite bootstrap and connection helpers."""

from pathlib import Path
import sqlite3


def bootstrap_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                player_count INTEGER NOT NULL,
                round_count INTEGER NOT NULL,
                current_round INTEGER NOT NULL,
                setup_stage TEXT NOT NULL,
                created_at TEXT,
                started_at TEXT,
                ended_at TEXT,
                config_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                player_no INTEGER NOT NULL,
                password_hash TEXT NOT NULL,
                password_plain TEXT NOT NULL DEFAULT '',
                company_name TEXT NOT NULL,
                home_city TEXT NOT NULL,
                setup_completed INTEGER NOT NULL,
                is_active INTEGER NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS cities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                city_name TEXT NOT NULL,
                loan_limit REAL NOT NULL,
                interest_rate REAL NOT NULL,
                engineer_salary_default REAL NOT NULL,
                material_cost REAL NOT NULL,
                market_size REAL NOT NULL,
                avg_price REAL NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS round_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                round_index INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                submitted_at TEXT,
                is_final INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(match_id, round_index, player_id)
            );
            CREATE TABLE IF NOT EXISTS round_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                round_index INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                override_json TEXT NOT NULL,
                bonus_penalty REAL NOT NULL,
                updated_at TEXT,
                UNIQUE(match_id, round_index, player_id)
            );
            CREATE TABLE IF NOT EXISTS round_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                round_index INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                summary_json TEXT NOT NULL,
                report_json TEXT NOT NULL,
                ranking_snapshot_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS round_city_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                round_index INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                city_name TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
