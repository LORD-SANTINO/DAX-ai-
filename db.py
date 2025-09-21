import os
import sqlite3
from typing import List, Dict, Optional
from cryptography.fernet import Fernet, InvalidToken

DB_PATH = os.getenv("DB_PATH", "clones.db")
MASTER_KEY = os.getenv("MASTER_KEY")  # must be set (Fernet key)

if MASTER_KEY is None:
    raise RuntimeError("MASTER_KEY environment variable is required to encrypt tokens. Generate with Fernet.generate_key().")

def get_fernet() -> Fernet:
    return Fernet(MASTER_KEY.encode())

def init_db(path: str = DB_PATH):
    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clones (
        user_id INTEGER PRIMARY KEY,
        bot_username TEXT,
        token_encrypted BLOB,
        instructions TEXT,
        active INTEGER DEFAULT 1
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        user_id INTEGER PRIMARY KEY,
        count INTEGER DEFAULT 0,
        verified INTEGER DEFAULT 0
    )
    """)
    conn.commit()
    return conn

_conn = init_db(DB_PATH)

def save_clone(user_id: int, token_plain: str, bot_username: str, instructions: str):
    f = get_fernet()
    token_enc = f.encrypt(token_plain.encode())
    cur = _conn.cursor()
    cur.execute("""
    INSERT INTO clones (user_id, bot_username, token_encrypted, instructions, active)
    VALUES (?, ?, ?, ?, 1)
    ON CONFLICT(user_id) DO UPDATE SET
        bot_username=excluded.bot_username,
        token_encrypted=excluded.token_encrypted,
        instructions=excluded.instructions,
        active=1
    """, (user_id, bot_username, token_enc, instructions))
    _conn.commit()

def deactivate_clone(user_id: int):
    cur = _conn.cursor()
    cur.execute("UPDATE clones SET active=0 WHERE user_id=?", (user_id,))
    _conn.commit()

def get_clone(user_id: int) -> Optional[Dict]:
    cur = _conn.cursor()
    cur.execute("SELECT user_id, bot_username, token_encrypted, instructions, active FROM clones WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        return None
    f = get_fernet()
    try:
        token = f.decrypt(row[2]).decode()
    except InvalidToken:
        raise RuntimeError("Failed to decrypt token: invalid MASTER_KEY or corrupted DB")
    return {"user_id": row[0], "bot_username": row[1], "token": token, "instructions": row[3], "active": bool(row[4])}

def list_active_clones() -> List[Dict]:
    cur = _conn.cursor()
    cur.execute("SELECT user_id FROM clones WHERE active=1")
    rows = cur.fetchall()
    results = []
    for (uid,) in rows:
        c = get_clone(uid)
        if c:
            results.append(c)
    return results
