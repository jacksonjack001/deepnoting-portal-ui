from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .settings import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                litellm_key TEXT NOT NULL,
                key_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        existing_user_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        user_migrations = {
            "password_hash": "ALTER TABLE users ADD COLUMN password_hash TEXT",
            "password_set_at": "ALTER TABLE users ADD COLUMN password_set_at TEXT",
            "last_login_at": "ALTER TABLE users ADD COLUMN last_login_at TEXT",
        }
        for column, statement in user_migrations.items():
            if column not in existing_user_columns:
                conn.execute(statement)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                out_trade_no TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                plan_id TEXT NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'plan',
                amount_cny TEXT NOT NULL,
                pay_type TEXT NOT NULL,
                trade_no TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                payment_url TEXT NOT NULL,
                plan_snapshot TEXT,
                activation_spec TEXT,
                raw_notify TEXT,
                activated_at TEXT,
                email_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        existing_order_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()
        }
        order_migrations = {
            "order_type": "ALTER TABLE orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'plan'",
            "plan_snapshot": "ALTER TABLE orders ADD COLUMN plan_snapshot TEXT",
            "activation_spec": "ALTER TABLE orders ADD COLUMN activation_spec TEXT",
            "refund_channel_fee_cny": "ALTER TABLE orders ADD COLUMN refund_channel_fee_cny TEXT",
            "refund_token_cost_cny": "ALTER TABLE orders ADD COLUMN refund_token_cost_cny TEXT",
            "refund_token_cost_usd": "ALTER TABLE orders ADD COLUMN refund_token_cost_usd TEXT",
            "refund_amount_cny": "ALTER TABLE orders ADD COLUMN refund_amount_cny TEXT",
            "refund_quote_json": "ALTER TABLE orders ADD COLUMN refund_quote_json TEXT",
            "refund_requested_at": "ALTER TABLE orders ADD COLUMN refund_requested_at TEXT",
        }
        for column, statement in order_migrations.items():
            if column not in existing_order_columns:
                conn.execute(statement)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                ref TEXT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_resets (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                spend_usd TEXT NOT NULL DEFAULT '0',
                request_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_threads (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                model TEXT NOT NULL,
                messages_json TEXT NOT NULL,
                last_usage_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                client TEXT NOT NULL,
                session_id TEXT NOT NULL,
                first_prompt TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                current_tool TEXT,
                current_step TEXT,
                model TEXT,
                context_window INTEGER,
                used_tokens INTEGER,
                remaining_context INTEGER,
                cwd TEXT,
                started_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, client, session_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_session_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(agent_session_id) REFERENCES agent_sessions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS page_views (
                page_key TEXT PRIMARY KEY,
                views INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def log_event(scope: str, message: str, ref: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (scope, ref, message, created_at) VALUES (?, ?, ?, ?)",
            (scope, ref, message, now_iso()),
        )


def increment_page_view(page_key: str) -> int:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO page_views (page_key, views, created_at, updated_at)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(page_key) DO UPDATE SET
                views = page_views.views + 1,
                updated_at = excluded.updated_at
            """,
            (page_key, timestamp, timestamp),
        )
        row = conn.execute("SELECT views FROM page_views WHERE page_key = ?", (page_key,)).fetchone()
        return int(row["views"] if row else 0)


def get_page_view(page_key: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT views FROM page_views WHERE page_key = ?", (page_key,)).fetchone()
        return int(row["views"] if row else 0)


def get_user_by_email(email: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
        return row_to_dict(row)


def get_user_by_litellm_key(litellm_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE litellm_key = ?", (litellm_key,)).fetchone()
        return row_to_dict(row)


def get_user(user_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return row_to_dict(row)


def create_user(email: str, litellm_key: str, key_status: str = "pending") -> dict:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users (email, litellm_key, key_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email.lower(), litellm_key, key_status, timestamp, timestamp),
        )
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
        return dict(row)


def set_user_password(user_id: int, password_hash: str) -> dict:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, password_set_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (password_hash, timestamp, timestamp, user_id),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row)


def mark_user_key_active(user_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE users SET key_status = 'active', updated_at = ? WHERE id = ?",
            (now_iso(), user_id),
        )


def mark_user_key_pending(user_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE users SET key_status = 'pending', updated_at = ? WHERE id = ?",
            (now_iso(), user_id),
        )


def mark_user_login(user_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), user_id),
        )


def create_order(
    out_trade_no: str,
    user_id: int,
    plan_id: str,
    order_type: str,
    amount_cny: str,
    pay_type: str,
    payment_url: str,
    plan_snapshot: str | None = None,
    activation_spec: str | None = None,
) -> dict:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO orders
                (
                    out_trade_no,
                    user_id,
                    plan_id,
                    order_type,
                    amount_cny,
                    pay_type,
                    payment_url,
                    plan_snapshot,
                    activation_spec,
                    created_at,
                    updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                out_trade_no,
                user_id,
                plan_id,
                order_type,
                amount_cny,
                pay_type,
                payment_url,
                plan_snapshot,
                activation_spec,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM orders WHERE out_trade_no = ?", (out_trade_no,)).fetchone()
        return dict(row)


def get_order(out_trade_no: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE out_trade_no = ?", (out_trade_no,)).fetchone()
        return row_to_dict(row)


def get_orders_for_email(email: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT o.*, u.email, u.key_status
            FROM orders o
            JOIN users u ON u.id = o.user_id
            WHERE u.email = ?
            ORDER BY o.id DESC
            LIMIT 20
            """,
            (email.lower(),),
        ).fetchall()
        return [dict(row) for row in rows]


def mark_order_paid(
    out_trade_no: str,
    trade_no: str | None,
    raw_notify: str,
    email_status: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status = 'paid',
                trade_no = COALESCE(?, trade_no),
                raw_notify = ?,
                activated_at = COALESCE(activated_at, ?),
                email_status = COALESCE(?, email_status),
                updated_at = ?
            WHERE out_trade_no = ?
            """,
            (trade_no, raw_notify, now_iso(), email_status, now_iso(), out_trade_no),
        )


def mark_order_failed(out_trade_no: str, raw_notify: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status = 'failed', raw_notify = ?, updated_at = ?
            WHERE out_trade_no = ?
            """,
            (raw_notify, now_iso(), out_trade_no),
        )


def mark_order_refund_pending(
    out_trade_no: str,
    *,
    channel_fee_cny: str,
    token_cost_cny: str,
    token_cost_usd: str,
    refund_amount_cny: str,
    quote_json: str,
) -> None:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status = 'refund_pending',
                refund_channel_fee_cny = ?,
                refund_token_cost_cny = ?,
                refund_token_cost_usd = ?,
                refund_amount_cny = ?,
                refund_quote_json = ?,
                refund_requested_at = ?,
                updated_at = ?
            WHERE out_trade_no = ?
            """,
            (
                channel_fee_cny,
                token_cost_cny,
                token_cost_usd,
                refund_amount_cny,
                quote_json,
                timestamp,
                timestamp,
                out_trade_no,
            ),
        )


def cancel_order_refund(out_trade_no: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status = 'paid',
                refund_channel_fee_cny = NULL,
                refund_token_cost_cny = NULL,
                refund_token_cost_usd = NULL,
                refund_amount_cny = NULL,
                refund_quote_json = NULL,
                refund_requested_at = NULL,
                updated_at = ?
            WHERE out_trade_no = ? AND status = 'refund_pending'
            """,
            (now_iso(), out_trade_no),
        )


def record_chat_usage(
    user_id: int,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    spend_usd: str,
    request_id: str | None,
) -> dict:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_usage
                (user_id, model, prompt_tokens, completion_tokens, total_tokens, spend_usd, request_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                model,
                int(prompt_tokens or 0),
                int(completion_tokens or 0),
                int(total_tokens or 0),
                spend_usd,
                request_id,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM chat_usage WHERE id = last_insert_rowid()").fetchone()
        return dict(row)


def get_chat_usage_for_email(email: str, start_at: str, end_at: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT cu.*, u.email
            FROM chat_usage cu
            JOIN users u ON u.id = cu.user_id
            WHERE u.email = ?
              AND cu.created_at >= ?
              AND cu.created_at < ?
            ORDER BY cu.id DESC
            LIMIT 1000
            """,
            (email.lower(), start_at, end_at),
        ).fetchall()
        return [dict(row) for row in rows]


def list_chat_threads(user_id: int, limit: int = 30) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM chat_threads
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def upsert_chat_thread(
    user_id: int,
    *,
    thread_id: str,
    title: str,
    model: str,
    messages_json: str,
    last_usage_json: str | None,
) -> dict:
    timestamp = now_iso()
    with connect() as conn:
        existing = conn.execute(
            "SELECT created_at FROM chat_threads WHERE id = ? AND user_id = ?",
            (thread_id, user_id),
        ).fetchone()
        created_at = existing["created_at"] if existing else timestamp
        conn.execute(
            """
            INSERT INTO chat_threads
                (id, user_id, title, model, messages_json, last_usage_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                model = excluded.model,
                messages_json = excluded.messages_json,
                last_usage_json = excluded.last_usage_json,
                updated_at = excluded.updated_at
            """,
            (thread_id, user_id, title, model, messages_json, last_usage_json, created_at, timestamp),
        )
        row = conn.execute("SELECT * FROM chat_threads WHERE id = ? AND user_id = ?", (thread_id, user_id)).fetchone()
        return dict(row)


def delete_chat_threads(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM chat_threads WHERE user_id = ?", (user_id,))


def upsert_agent_event(
    user_id: int,
    *,
    client: str,
    session_id: str,
    event_type: str,
    payload_json: str,
    first_prompt: str | None = None,
    status: str | None = None,
    current_tool: str | None = None,
    current_step: str | None = None,
    model: str | None = None,
    context_window: int | None = None,
    used_tokens: int | None = None,
    remaining_context: int | None = None,
    cwd: str | None = None,
    finished: bool = False,
) -> dict:
    timestamp = now_iso()
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE user_id = ? AND client = ? AND session_id = ?
            """,
            (user_id, client, session_id),
        ).fetchone()
        started_at = existing["started_at"] if existing else timestamp
        existing_first_prompt = existing["first_prompt"] if existing else None
        next_first_prompt = existing_first_prompt or first_prompt
        finished_at = timestamp if finished else (existing["finished_at"] if existing else None)
        conn.execute(
            """
            INSERT INTO agent_sessions
                (
                    user_id,
                    client,
                    session_id,
                    first_prompt,
                    status,
                    current_tool,
                    current_step,
                    model,
                    context_window,
                    used_tokens,
                    remaining_context,
                    cwd,
                    started_at,
                    last_seen_at,
                    finished_at,
                    updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, client, session_id) DO UPDATE SET
                first_prompt = COALESCE(agent_sessions.first_prompt, excluded.first_prompt),
                status = COALESCE(excluded.status, agent_sessions.status),
                current_tool = COALESCE(excluded.current_tool, agent_sessions.current_tool),
                current_step = COALESCE(excluded.current_step, agent_sessions.current_step),
                model = COALESCE(excluded.model, agent_sessions.model),
                context_window = COALESCE(excluded.context_window, agent_sessions.context_window),
                used_tokens = COALESCE(excluded.used_tokens, agent_sessions.used_tokens),
                remaining_context = COALESCE(excluded.remaining_context, agent_sessions.remaining_context),
                cwd = COALESCE(excluded.cwd, agent_sessions.cwd),
                last_seen_at = excluded.last_seen_at,
                finished_at = COALESCE(excluded.finished_at, agent_sessions.finished_at),
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                client,
                session_id,
                next_first_prompt,
                status or "running",
                current_tool,
                current_step,
                model,
                context_window,
                used_tokens,
                remaining_context,
                cwd,
                started_at,
                timestamp,
                finished_at,
                timestamp,
            ),
        )
        session = conn.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE user_id = ? AND client = ? AND session_id = ?
            """,
            (user_id, client, session_id),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO agent_events (agent_session_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session["id"], event_type, payload_json, timestamp),
        )
        row = conn.execute(
            """
            SELECT
                s.*,
                (
                    SELECT COUNT(*)
                    FROM agent_events e
                    WHERE e.agent_session_id = s.id
                ) AS event_count
            FROM agent_sessions s
            WHERE s.id = ?
            """,
            (session["id"],),
        ).fetchone()
        return dict(row)


def list_agent_sessions(user_id: int, limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                s.*,
                (
                    SELECT COUNT(*)
                    FROM agent_events e
                    WHERE e.agent_session_id = s.id
                ) AS event_count
            FROM agent_sessions s
            WHERE s.user_id = ?
            ORDER BY s.last_seen_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_agent_session(user_id: int, session_pk: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                s.*,
                (
                    SELECT COUNT(*)
                    FROM agent_events e
                    WHERE e.agent_session_id = s.id
                ) AS event_count
            FROM agent_sessions s
            WHERE s.user_id = ? AND s.id = ?
            """,
            (user_id, session_pk),
        ).fetchone()
        return row_to_dict(row)


def list_agent_events(user_id: int, session_pk: int, limit: int = 200) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT e.*
            FROM agent_events e
            JOIN agent_sessions s ON s.id = e.agent_session_id
            WHERE s.user_id = ? AND s.id = ?
            ORDER BY e.created_at ASC, e.id ASC
            LIMIT ?
            """,
            (user_id, session_pk, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def create_session(token: str, user_id: int, expires_at: str) -> None:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (token, user_id, expires_at, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token, user_id, expires_at, timestamp, timestamp),
        )


def get_session_user(token: str, extend_seconds: int | None = None) -> dict | None:
    timestamp = now_iso()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.*
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, timestamp),
        ).fetchone()
        if row:
            if extend_seconds:
                next_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=extend_seconds)).isoformat()
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token = ?",
                    (timestamp, next_expires_at, token),
                )
            else:
                conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token = ?", (timestamp, token))
        return row_to_dict(row)


def delete_session(token: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def create_password_reset(token: str, user_id: int, expires_at: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO password_resets (token, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, user_id, expires_at, now_iso()),
        )


def get_password_reset(token: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT pr.*, u.email
            FROM password_resets pr
            JOIN users u ON u.id = pr.user_id
            WHERE pr.token = ? AND pr.used_at IS NULL AND pr.expires_at > ?
            """,
            (token, now_iso()),
        ).fetchone()
        return row_to_dict(row)


def consume_password_reset(token: str, password_hash: str) -> dict | None:
    timestamp = now_iso()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT pr.*, u.email
            FROM password_resets pr
            JOIN users u ON u.id = pr.user_id
            WHERE pr.token = ? AND pr.used_at IS NULL AND pr.expires_at > ?
            """,
            (token, timestamp),
        ).fetchone()
        if not row:
            return None
        user_id = row["user_id"]
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, password_set_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (password_hash, timestamp, timestamp, user_id),
        )
        conn.execute("UPDATE password_resets SET used_at = ? WHERE token = ?", (timestamp, token))
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return row_to_dict(user)


def purge_expired_auth_rows() -> None:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (timestamp,))
        conn.execute("DELETE FROM password_resets WHERE expires_at <= ? OR used_at IS NOT NULL", (timestamp,))
