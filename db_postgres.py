import os
import time
from typing import List, Tuple, Optional

from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    BigInteger,
    String,
    Boolean,
    Integer,
    select,
    text,
)
from sqlalchemy.engine import Engine

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required (postgresql).")

# Create engine (uses SQLAlchemy with psycopg2). Adjust pool settings as needed.
_engine: Engine = create_engine(DATABASE_URL, pool_pre_ping=True)
_metadata = MetaData()

users = Table(
    "users",
    _metadata,
    Column("chat_id", BigInteger, primary_key=True),
    Column("username", String),
    Column("first_seen", Integer),
    Column("last_seen", Integer),
    Column("opted_out", Boolean, default=False),
)

# Ensure table exists
def init_db() -> None:
    _metadata.create_all(_engine)


# Initialize right away
init_db()


def insert_or_update_user(chat_id: int, username: Optional[str]) -> None:
    """
    Insert a new user or update username/last_seen. Uses Postgres ON CONFLICT.
    """
    now = int(time.time())
    sql = text(
        """
        INSERT INTO users (chat_id, username, first_seen, last_seen, opted_out)
        VALUES (:chat_id, :username, :first_seen, :last_seen, false)
        ON CONFLICT (chat_id) DO UPDATE
        SET username = EXCLUDED.username,
            last_seen = EXCLUDED.last_seen
        """
    )
    with _engine.begin() as conn:
        conn.execute(
            sql,
            {
                "chat_id": chat_id,
                "username": username or "",
                "first_seen": now,
                "last_seen": now,
            },
        )


def get_all_subscribed_user_ids() -> List[int]:
    """Return chat_ids of users who have not opted out."""
    stmt = select(users.c.chat_id).where(users.c.opted_out == False)  # noqa: E712
    with _engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [int(r[0]) for r in rows]


def get_user_count(include_opted_out: bool = False) -> int:
    if include_opted_out:
        sql = text("SELECT COUNT(*) FROM users")
    else:
        sql = text("SELECT COUNT(*) FROM users WHERE opted_out = false")
    with _engine.connect() as conn:
        return int(conn.execute(sql).scalar() or 0)


def remove_user(chat_id: int) -> None:
    sql = text("DELETE FROM users WHERE chat_id = :chat_id")
    with _engine.begin() as conn:
        conn.execute(sql, {"chat_id": chat_id})


def opt_out_user(chat_id: int) -> None:
    sql = text("UPDATE users SET opted_out = true WHERE chat_id = :chat_id")
    with _engine.begin() as conn:
        conn.execute(sql, {"chat_id": chat_id})


def get_users_paginated(page: int = 1, page_size: int = 20, include_opted_out: bool = False) -> Tuple[List[Tuple], int]:
    """
    Returns (rows, total_count)
    rows: (chat_id, username, first_seen, last_seen, opted_out)
    """
    offset = max(0, (page - 1) * page_size)
    if include_opted_out:
        rows_sql = text(
            "SELECT chat_id, username, first_seen, last_seen, opted_out FROM users ORDER BY last_seen DESC LIMIT :limit OFFSET :offset"
        )
        count_sql = text("SELECT COUNT(*) FROM users")
    else:
        rows_sql = text(
            "SELECT chat_id, username, first_seen, last_seen, opted_out FROM users WHERE opted_out = false ORDER BY last_seen DESC LIMIT :limit OFFSET :offset"
        )
        count_sql = text("SELECT COUNT(*) FROM users WHERE opted_out = false")

    with _engine.connect() as conn:
        rows = conn.execute(rows_sql, {"limit": page_size, "offset": offset}).fetchall()
        total = int(conn.execute(count_sql).scalar() or 0)

    return [tuple(r) for r in rows], total
