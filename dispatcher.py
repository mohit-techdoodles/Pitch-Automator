import os
import re
import smtplib
import random
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, formatdate
from typing import Dict, Any, List, Tuple, Optional, Callable
import sqlite3

from db import get_db_connection, utc_now, utc_now_iso, get_daily_sent_count, get_due_leads


def test_smtp_connection(config: Dict[str, Any]) -> Tuple[bool, str]:
    """Test SMTP connection and authentication."""
    host = config.get("SMTP_HOST")
    port = int(config.get("SMTP_PORT", 587))
    user = config.get("SENDER_EMAIL")
    password = config.get("MAIL_APP_PASSWORD")

    if not host or not user or not password:
        return False, "Missing SMTP Host, Sender Email, or Mail App Password."

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(user, password)
        server.quit()
        return True, f"Successfully connected to {host}:{port} and authenticated as {user}."
    except Exception as e:
        return False, f"SMTP Connection failed: {str(e)}"


def remaining_daily_quota(db_path: str, daily_limit: int) -> int:
    """Calculate remaining quota for today (UTC)."""
    sent_today = get_daily_sent_count(db_path)
    return max(0, daily_limit - sent_today)


def format_template(text: str, variables: Dict[str, str]) -> str:
    """Safely format template strings with fallback for missing keys."""
    class SafeDict(dict):
        def __missing__(self, key):
            return ""

    return text.format_map(SafeDict(variables))


def sanitize_company_name(name: Optional[str]) -> str:
    """
    Intelligently sanitize company names for human-like cold outreach:
    1. Removes legal entity suffixes (e.g. Pvt Ltd, Inc., LLC, GmbH, Corp).
    2. Removes domain extensions (e.g. Acme.com -> Acme).
    3. Cleans taglines/separators (e.g. Acme - Cloud Services -> Acme).
    4. Strips trailing commas, dots, and normalizes ALL-CAPS names.
    """
    if not name or not str(name).strip():
        return "your company"

    clean = str(name).strip()

    # 1. Remove URLs/domain extensions if the company was scraped as a domain/URL
    clean = re.sub(r"^https?://(?:www\.)?", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\.(?:com|io|co\.uk|org|net|ai|in|de|ca|dev|tech|app|biz|info)/?$", "", clean, flags=re.IGNORECASE)

    # 2. Strip tagline separators like " | ", " - ", " – ", " — ", " : "
    for sep in [" | ", " - ", " – ", " — ", " : ", " // "]:
        if sep in clean:
            clean = clean.split(sep)[0].strip()

    # 3. Strip legal identifiers and business entities
    legal_patterns = [
        r",?\s*\b(?:pvt\.?\s*ltd\.?|private\s+limited)\b",
        r",?\s*\b(?:pty\.?\s*ltd\.?|proprietary\s+limited)\b",
        r",?\s*\b(?:l\.?l\.?c\.?|limited\s+liability\s+company)\b",
        r",?\s*\b(?:l\.?l\.?p\.?|limited\s+liability\s+partnership)\b",
        r",?\s*\b(?:inc\.?|incorporated)\b",
        r",?\s*\b(?:corp\.?|corporation)\b",
        r",?\s*\b(?:ltd\.?|limited)\b",
        r",?\s*\b(?:co\.?|company)\b",
        r",?\s*\b(?:gmbh|s\.a\.?|s\.r\.l\.?|b\.v\.?|plc)\b",
    ]
    for pat in legal_patterns:
        clean = re.sub(pat, "", clean, flags=re.IGNORECASE).strip()

    # 4. Remove parenthetical qualifiers, e.g. "Acme (US)", "TechFlow (HQ)"
    clean = re.sub(r"\s*\([^)]*\)", "", clean).strip()

    # 5. Remove trailing punctuation: commas, dashes, dots
    clean = re.sub(r"[\s,\.\-_/]+$", "", clean).strip()

    # 6. Normalize casing if ALL-CAPS (e.g. "TECHFLOW INNOVATIONS" -> "Techflow Innovations")
    if clean.isupper() and len(clean) > 3:
        clean = clean.title()

    return clean if clean else "your company"


def build_message(
    lead: Dict[str, Any],
    step_num: int,
    step_config: Dict[str, Any],
    sender_email: str,
    sender_name: str,
) -> Tuple[EmailMessage, str]:
    """
    Build RFC 5322 compliant plain-text email with proper threading headers.
    Returns: (EmailMessage, generated_message_id)
    """
    domain = sender_email.split("@")[-1] if "@" in sender_email else "localhost"
    generated_msg_id = make_msgid(domain=domain)

    # Sanitize company name for clean human subject lines and greetings
    clean_company = sanitize_company_name(lead.get("company"))

    # Variables for placeholder formatting
    vars_dict = {
        "company": clean_company,
        "raw_company": lead.get("company") or "",
        "website": lead.get("website") or "",
        "country": lead.get("country") or "",
        "email": lead.get("email") or "",
        "sender_name": sender_name or "",
        "sender_email": sender_email,
    }

    raw_subject = step_config.get("subject", "Quick note for {company}")
    raw_body = step_config.get("body", "Hi {company} team,\n\nBest,\n{sender_name}")

    subject = format_template(raw_subject, vars_dict)
    body = format_template(raw_body, vars_dict)

    msg = EmailMessage()
    msg.set_content(body)  # Plain-text content

    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender_email)) if sender_name else sender_email
    msg["To"] = lead["email"]
    msg["Message-ID"] = generated_msg_id
    msg["Date"] = formatdate(localtime=True)

    # Threading headers for follow-ups
    root_id = lead.get("root_message_id")
    last_id = lead.get("last_message_id")

    if step_num > 1 and last_id:
        msg["In-Reply-To"] = last_id
        if root_id:
            msg["References"] = f"{root_id} {last_id}" if root_id != last_id else root_id
        else:
            msg["References"] = last_id

    return msg, generated_msg_id


def send_test_email(config: Dict[str, Any], recipient_email: str) -> Tuple[bool, str]:
    """Send an immediate test email to verify deliverability and layout."""
    host = config.get("SMTP_HOST")
    port = int(config.get("SMTP_PORT", 587))
    sender_email = config.get("SENDER_EMAIL")
    sender_name = config.get("SENDER_NAME", "")
    password = config.get("MAIL_APP_PASSWORD")

    if not sender_email or not password:
        return False, "SENDER_EMAIL or MAIL_APP_PASSWORD not configured."

    msg = EmailMessage()
    domain = sender_email.split("@")[-1] if "@" in sender_email else "localhost"
    msg_id = make_msgid(domain=domain)

    body = (
        f"Hi,\n\n"
        f"This is a test plain-text email sent from Python Pitch Automator.\n\n"
        f"If you received this in your Primary inbox, your SMTP configuration and STARTTLS "
        f"credentials are functioning properly.\n\n"
        f"Sent at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Message-ID: {msg_id}\n\n"
        f"Best,\n{sender_name}"
    )
    msg.set_content(body)
    msg["Subject"] = "Pitch Automator - Test Message"
    msg["From"] = formataddr((sender_name, sender_email)) if sender_name else sender_email
    msg["To"] = recipient_email
    msg["Message-ID"] = msg_id
    msg["Date"] = formatdate(localtime=True)

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(sender_email, password)
        server.send_message(msg)
        server.quit()
        return True, f"Test email successfully delivered to {recipient_email}!"
    except Exception as e:
        return False, f"Failed to send test email: {str(e)}"


def run_pitch_dispatch(
    db_path: str,
    config: Dict[str, Any],
    sequences: Dict[str, Any],
    max_to_send: Optional[int] = None,
    dry_run: bool = False,
    sleep_between: bool = True,
    ignore_delay: bool = False,
    log_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    """
    Run due pitches batch:
    1. Check remaining daily quota
    2. Query due leads
    3. Build plain-text RFC 5322 message
    4. Dispatch over SMTP
    5. Persist immediately to send_log and leads table
    6. Sleep randomized interval (if not dry_run or sleep_between=True)
    """
    def log(m: str):
        if log_callback:
            log_callback(m)

    daily_limit = int(config.get("DAILY_SEND_LIMIT", 40))
    min_delay = int(config.get("MIN_DELAY_SEC", 180))
    max_delay = int(config.get("MAX_DELAY_SEC", 420))
    sender_email = config.get("SENDER_EMAIL", "")
    sender_name = config.get("SENDER_NAME", "")
    password = config.get("MAIL_APP_PASSWORD", "")
    host = config.get("SMTP_HOST", "smtp.gmail.com")
    port = int(config.get("SMTP_PORT", 587))

    quota = remaining_daily_quota(db_path, daily_limit)
    log(f"📊 Daily quota remaining for today: {quota}/{daily_limit}")

    if quota <= 0:
        log("⛔ Daily send limit reached for today. Halting outreach until tomorrow.")
        return {"sent": 0, "quota_left": 0, "reason": "Quota reached"}

    limit_to_fetch = quota
    if max_to_send is not None and max_to_send > 0:
        limit_to_fetch = min(quota, max_to_send)

    total_sequence_steps = len(sequences)

    # Fetch due leads using db query
    due_leads = get_due_leads(
        db_path=db_path,
        max_step=total_sequence_steps,
        limit=limit_to_fetch,
        ignore_delay=ignore_delay,
    )

    if not due_leads:
        log("✅ No leads are currently due for sending.")
        return {"sent": 0, "quota_left": quota, "reason": "No due leads"}

    log(f"🎯 Found {len(due_leads)} lead(s) ready to process.")
    conn = get_db_connection(db_path)

    # Initialize SMTP server connection if not dry run
    server = None
    if not dry_run:
        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=20)
            else:
                server = smtplib.SMTP(host, port, timeout=20)
                server.ehlo()
                server.starttls()
                server.ehlo()
            server.login(sender_email, password)
            log(f"🔌 Connected & authenticated to SMTP server {host}:{port}")
        except Exception as e:
            err = f"Failed to connect to SMTP server: {str(e)}"
            log(f"❌ {err}")
            conn.close()
            return {"sent": 0, "error": err}

    sent_count = 0
    total_to_process = len(due_leads)

    try:
        for idx, lead in enumerate(due_leads):
            email_addr = lead["email"]
            current_step = lead["current_step"]
            next_step = current_step + 1
            step_key = str(next_step)

            if step_key not in sequences:
                log(f"⚠️ No sequence template found for step {next_step}. Skipping {email_addr}.")
                continue

            step_cfg = sequences[step_key]
            msg, generated_msg_id = build_message(
                lead=lead,
                step_num=next_step,
                step_config=step_cfg,
                sender_email=sender_email,
                sender_name=sender_name,
            )

            log(f"✉️ [{idx+1}/{total_to_process}] Preparing Step {next_step} for {email_addr} ({lead.get('company')})...")

            if dry_run:
                log(f"   [DRY-RUN] Subject: {msg['Subject']}")
                log(f"   [DRY-RUN] Simulated send to {email_addr} with Message-ID: {generated_msg_id}")
            else:
                try:
                    server.send_message(msg)
                    log(f"   ✅ SMTP 250 OK: Sent Step {next_step} to {email_addr}")
                except Exception as send_err:
                    log(f"   ❌ SMTP Error sending to {email_addr}: {str(send_err)}")
                    continue

            # Persist to database immediately
            now_dt = utc_now()
            today_utc = now_dt.strftime("%Y-%m-%d")
            now_iso_str = now_dt.isoformat()

            # Record send_log
            conn.execute(
                """
                INSERT INTO send_log (email, step_number, message_id, sent_date_utc, sent_at_utc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (email_addr, next_step, generated_msg_id, today_utc, now_iso_str),
            )

            # Determine next lead state
            root_id = lead["root_message_id"] or generated_msg_id
            if next_step < total_sequence_steps:
                # Schedule follow up
                next_step_cfg = sequences.get(str(next_step + 1), {})
                delay_days = next_step_cfg.get("delay_days", 3)
                next_send_dt = now_dt + timedelta(days=delay_days)
                next_send_iso = next_send_dt.isoformat()
                new_status = "IN_SEQUENCE"
            else:
                next_send_iso = None
                new_status = "COMPLETED"

            conn.execute(
                """
                UPDATE leads
                SET current_step = ?,
                    status = ?,
                    root_message_id = ?,
                    last_message_id = ?,
                    next_send_at = ?,
                    updated_at = ?
                WHERE email = ?
                """,
                (next_step, new_status, root_id, generated_msg_id, next_send_iso, now_iso_str, email_addr),
            )
            conn.commit()
            sent_count += 1

            if progress_callback:
                progress_callback(idx + 1, total_to_process)

            # Throttle delay between sends
            if idx < total_to_process - 1 and sleep_between:
                sleep_sec = random.randint(min_delay, max_delay) if not dry_run else 1
                log(f"⏳ Sleeping {sleep_sec}s before next send to protect domain reputation...")
                time.sleep(sleep_sec)

    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass
        conn.close()

    new_quota = remaining_daily_quota(db_path, daily_limit)
    log(f"🎉 Batch complete. Sent {sent_count} email(s). Remaining quota today: {new_quota}/{daily_limit}.")
    return {"sent": sent_count, "quota_left": new_quota}
