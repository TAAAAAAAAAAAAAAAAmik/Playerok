import sqlite3
from config import DB_PATH


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen_orders (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen_complaints (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def is_seen(table: str, item_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"SELECT 1 FROM {table} WHERE id = ?", (item_id,))
    result = c.fetchone() is not None
    conn.close()
    return result


def mark_seen(table: str, item_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"INSERT OR IGNORE INTO {table} (id) VALUES (?)", (item_id,))
    conn.commit()
    conn.close()


def get_stats() -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM seen_orders")
    orders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM seen_complaints")
    complaints = c.fetchone()[0]
    conn.close()
    return {"orders": orders, "complaints": complaints}
