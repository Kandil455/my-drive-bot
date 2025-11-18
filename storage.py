import asyncio
import datetime
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

DB_PATH = Path(os.environ.get("BOT_DB_PATH", "bot_data.db"))


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                username TEXT,
                phone TEXT,
                phone_shared_at TEXT,
                team TEXT,
                email TEXT,
                shared_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_team ON users(team)
            """
        )
        conn.commit()


def _run_in_thread(fn, *args, **kwargs):
    return asyncio.to_thread(fn, *args, **kwargs)


def _ensure_user_sync(telegram_id: int, first_name: str, last_name: str, username: str, phone: str) -> None:
    now = datetime.datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, first_name, last_name, username, phone, phone_shared_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                username=excluded.username,
                phone=excluded.phone,
                phone_shared_at=excluded.phone_shared_at,
                updated_at=excluded.updated_at
            """,
            (telegram_id, first_name, last_name, username, phone, now, now),
        )
        conn.commit()


def ensure_user(telegram_id: int, first_name: str, last_name: str, username: str, phone: str) -> None:
    return _run_in_thread(_ensure_user_sync, telegram_id, first_name, last_name, username, phone)


def _update_field_sync(telegram_id: int, field: str, value: str) -> None:
    now = datetime.datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            f"""
            UPDATE users SET {field} = ?, updated_at = ? WHERE telegram_id = ?
            """,
            (value, now, telegram_id),
        )
        conn.commit()


def update_team(telegram_id: int, team: str) -> None:
    return _run_in_thread(_update_field_sync, telegram_id, "team", team)


def update_email(telegram_id: int, email: str) -> None:
    return _run_in_thread(_update_field_sync, telegram_id, "email", email)


def _record_share_sync(telegram_id: int) -> None:
    now = datetime.datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users SET shared_at = ?, updated_at = ? WHERE telegram_id = ?
            """,
            (now, now, telegram_id),
        )
        conn.commit()


def record_share(telegram_id: int) -> None:
    return _run_in_thread(_record_share_sync, telegram_id)


def _fetch_user_sync(telegram_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        return dict(row) if row else None


def get_user(telegram_id: int) -> Optional[dict]:
    return _run_in_thread(_fetch_user_sync, telegram_id)


def _team_emails_sync(team: str) -> List[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT email FROM users WHERE team = ? AND email IS NOT NULL",
            (team,),
        ).fetchall()
        return [row["email"] for row in rows if row["email"]]


def team_emails(team: str) -> List[str]:
    return _run_in_thread(_team_emails_sync, team)


def _all_teams_with_counts_sync() -> List[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT team, COUNT(*) as total, SUM(CASE WHEN shared_at IS NOT NULL THEN 1 ELSE 0 END) as added FROM users WHERE team IS NOT NULL GROUP BY team"
        ).fetchall()
        return [
            {"team": row["team"], "total": row["total"], "added": row["added"]}
            for row in rows
        ]


def all_teams_with_counts() -> List[dict]:
    return _run_in_thread(_all_teams_with_counts_sync)


def _fetch_all_users_sync() -> List[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT telegram_id, first_name, last_name, username, phone, email, team, shared_at
            FROM users
            """
        ).fetchall()
        return [dict(row) for row in rows]


def all_users() -> List[dict]:
    return _run_in_thread(_fetch_all_users_sync)
