import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return current UTC datetime in ISO 8601 format."""
    return utc_now().isoformat()


def get_db_connection(db_path: str) -> sqlite3.Connection:
    """Connect to SQLite database with row factory enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Initialize SQLite database with required tables from developer documentation."""
    with get_db_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                email TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                country TEXT DEFAULT '',
                website TEXT DEFAULT '',
                current_step INTEGER DEFAULT 0,
                status TEXT DEFAULT 'PENDING',
                next_send_at TEXT,
                root_message_id TEXT,
                last_message_id TEXT,
                updated_at TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS send_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                sent_date_utc TEXT NOT NULL,
                sent_at_utc TEXT NOT NULL
            );
            """
        )
        conn.commit()


def _extract_lead_field(row: Dict[str, Any], candidate_keys: List[str]) -> str:
    """
    Intelligently find field value using case-insensitive and substring matching.
    Filters out placeholder values like 'Not listed', 'Not found', '—', etc.
    """
    lower_map = {str(k).strip().lower(): str(v).strip() for k, v in row.items() if v is not None}
    invalid_placeholders = {"not listed", "not found", "not found on maps", "none", "null", "n/a", "—", "-"}

    # 1. Exact match pass
    for cand in candidate_keys:
        cand_l = cand.lower()
        if cand_l in lower_map and lower_map[cand_l]:
            val = lower_map[cand_l]
            if val.lower() not in invalid_placeholders:
                return val

    # 2. Substring match pass
    for cand in candidate_keys:
        cand_l = cand.lower()
        for k, val in lower_map.items():
            if cand_l in k and val:
                if val.lower() not in invalid_placeholders:
                    return val

    return ""


def import_leads_from_rows(db_path: str, rows: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    Insert leads from row dictionaries using INSERT OR IGNORE so existing states are preserved.
    Supports standard CSVs as well as Lead Scrapper hotel exports (Hotel Name, Direct Email, etc.).
    Returns: (inserted_count, ignored_count)
    """
    init_db(db_path)
    now_iso = utc_now_iso()
    inserted = 0
    ignored = 0

    email_aliases = ["direct email", "email", "mail", "e-mail", "contact email", "email address"]
    company_aliases = ["hotel name", "company", "business name", "place name", "organization", "hotel", "name"]
    website_aliases = ["official website", "website", "url", "web", "site", "domain"]
    country_aliases = ["country", "city", "street address", "location", "state", "address"]

    with get_db_connection(db_path) as conn:
        for r in rows:
            email = _extract_lead_field(r, email_aliases).lower()
            company = _extract_lead_field(r, company_aliases)
            country = _extract_lead_field(r, country_aliases)
            website = _extract_lead_field(r, website_aliases)

            # Skip rows where no valid email was found or extracted
            if not email or "@" not in email:
                continue

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO leads (
                    email, company, country, website, current_step, status, next_send_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 'PENDING', ?, ?)
                """,
                (email, company or "there", country, website, now_iso, now_iso),
            )
            if cursor.rowcount > 0:
                inserted += 1
            else:
                ignored += 1
        conn.commit()

    return inserted, ignored


def import_csv_file(db_path: str, csv_filepath: str) -> Tuple[int, int]:
    """Import leads from a CSV file path."""
    if not Path(csv_filepath).exists():
        return 0, 0

    rows = []
    with open(csv_filepath, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Normalize headers (lowercase, trimmed)
            clean_row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
            rows.append(clean_row)

    return import_leads_from_rows(db_path, rows)


def get_lead_stats(db_path: str) -> Dict[str, int]:
    """Get count of leads per status."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) as cnt
            FROM leads
            GROUP BY status
            """
        ).fetchall()
        total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

    stats = {
        "TOTAL": total_leads,
        "PENDING": 0,
        "IN_SEQUENCE": 0,
        "REPLIED": 0,
        "BOUNCED": 0,
        "UNSUBSCRIBED": 0,
        "COMPLETED": 0,
    }
    for r in rows:
        st = r["status"]
        if st in stats:
            stats[st] = r["cnt"]
        else:
            stats[st] = r["cnt"]
    return stats


def get_daily_sent_count(db_path: str, date_utc: Optional[str] = None) -> int:
    """Return count of emails sent for a given UTC date (defaults to today)."""
    init_db(db_path)
    if not date_utc:
        date_utc = utc_now().strftime("%Y-%m-%d")

    with get_db_connection(db_path) as conn:
        (cnt,) = conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE sent_date_utc = ?", (date_utc,)
        ).fetchone()
        return cnt


def get_leads(
    db_path: str,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Retrieve filtered leads list."""
    init_db(db_path)
    query = "SELECT * FROM leads WHERE 1=1"
    params: List[Any] = []

    if status and status != "ALL":
        query += " AND status = ?"
        params.append(status)

    if search:
        query += " AND (email LIKE ? OR company LIKE ? OR website LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term])

    query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_due_leads(
    db_path: str, max_step: int = 3, limit: Optional[int] = None, ignore_delay: bool = False
) -> List[Dict[str, Any]]:
    """
    Get leads that are due to receive their next pitch sequence:
    1. PENDING leads (ready for Step 1)
    2. IN_SEQUENCE leads (ready for Step 2 or 3)
    If ignore_delay is True, ignores next_send_at timestamp (useful for testing or immediate sends).
    """
    init_db(db_path)
    now_iso = utc_now_iso()

    if ignore_delay:
        query = """
            SELECT * FROM leads
            WHERE (
                status = 'PENDING'
                OR
                (status = 'IN_SEQUENCE' AND current_step < ?)
            )
            ORDER BY current_step ASC, updated_at ASC
        """
        params = [max_step]
    else:
        query = """
            SELECT * FROM leads
            WHERE (
                (status = 'PENDING' AND (next_send_at IS NULL OR next_send_at <= ?))
                OR
                (status = 'IN_SEQUENCE' AND next_send_at <= ? AND current_step < ?)
            )
            ORDER BY current_step DESC, next_send_at ASC
        """
        params = [now_iso, now_iso, max_step]

    if limit is not None and limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    with get_db_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_lead_status(db_path: str, email: str, new_status: str) -> None:
    """Manually update a lead's status (e.g. UNSUBSCRIBED, REPLIED)."""
    now_iso = utc_now_iso()
    with get_db_connection(db_path) as conn:
        conn.execute(
            "UPDATE leads SET status = ?, updated_at = ? WHERE email = ?",
            (new_status, now_iso, email),
        )
        conn.commit()


def get_recent_send_logs(db_path: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve recent entries from send_log."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM send_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
