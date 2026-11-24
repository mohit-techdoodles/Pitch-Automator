import imaplib
import email
from email.header import decode_header
import sqlite3
from typing import Dict, Any, List, Tuple
from db import get_db_connection, utc_now_iso


def test_imap_connection(config: Dict[str, Any]) -> Tuple[bool, str]:
    """Test IMAP connection and credentials."""
    host = config.get("IMAP_HOST")
    port = int(config.get("IMAP_PORT", 993))
    user = config.get("SENDER_EMAIL")
    password = config.get("MAIL_APP_PASSWORD")

    if not host or not user or not password:
        return False, "Missing IMAP Host, Sender Email, or Mail App Password."

    try:
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, password)
            status, _ = imap.select("INBOX", readonly=True)
            if status == "OK":
                return True, f"Successfully connected to {host}:{port} and authenticated as {user}."
            return False, f"Connected to IMAP, but failed to select INBOX (status: {status})."
    except Exception as e:
        return False, f"IMAP Connection failed: {str(e)}"


def _clean_msg_id(raw_id: str) -> str:
    """Normalize message id for IMAP searching."""
    if not raw_id:
        return ""
    return raw_id.strip("<> \t\n\r")


def _is_unsubscribe_reply(text_content: str) -> bool:
    """Check if prospect reply is an opt-out request."""
    lowered = text_content.lower()
    keywords = ["stop", "unsubscribe", "remove me", "not interested", "opt out", "please remove"]
    return any(k in lowered for k in keywords)


def sync_inbox_events(
    db_path: str, config: Dict[str, Any], log_callback=None
) -> List[Dict[str, str]]:
    """
    Poll IMAP inbox for bounces and replies for active leads.
    Matches incoming replies by In-Reply-To header or sender address.
    Detects bounce notices from mailer-daemon / postmaster.
    """
    def log(msg: str):
        if log_callback:
            log_callback(msg)

    results: List[Dict[str, str]] = []

    host = config.get("IMAP_HOST")
    port = int(config.get("IMAP_PORT", 993))
    user = config.get("SENDER_EMAIL")
    password = config.get("MAIL_APP_PASSWORD")

    if not host or not user or not password:
        log("⚠️ IMAP credentials not configured. Skipping inbox sync.")
        return results

    conn = get_db_connection(db_path)
    try:
        active = conn.execute(
            """
            SELECT email, company, last_message_id, root_message_id
            FROM leads
            WHERE status = 'IN_SEQUENCE' AND last_message_id IS NOT NULL
            """
        ).fetchall()

        if not active:
            log("ℹ️ No leads currently in 'IN_SEQUENCE' awaiting reply checks.")
            return results

        log(f"🔄 Connecting to IMAP {host}:{port} to check events for {len(active)} active leads...")
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, password)
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                log(f"❌ Failed to select INBOX: {status}")
                return results

            now_iso = utc_now_iso()

            for row in active:
                lead_email = row["email"]
                msg_id = row["last_message_id"] or ""
                clean_id = _clean_msg_id(msg_id)

                # 1. Check for non-delivery bounce reports mentioning the lead address
                # Search mailer-daemon or postmaster mentioning lead email in body
                bounce_queries = [
                    f'(FROM "mailer-daemon" BODY "{lead_email}")',
                    f'(FROM "postmaster" BODY "{lead_email}")',
                ]
                is_bounced = False
                for b_q in bounce_queries:
                    try:
                        b_status, b_data = imap.search(None, b_q)
                        if b_status == "OK" and b_data[0].split():
                            is_bounced = True
                            break
                    except Exception:
                        pass

                if is_bounced:
                    conn.execute(
                        "UPDATE leads SET status='BOUNCED', updated_at=? WHERE email=?",
                        (now_iso, lead_email),
                    )
                    conn.commit()
                    log(f"🔴 Bounce detected for {lead_email}. Marked as BOUNCED.")
                    results.append({"email": lead_email, "type": "BOUNCED", "detail": "NDR Bounce detected"})
                    continue

                # 2. Check for direct replies referencing our Message-ID or from the lead's address
                has_replied = False
                matched_msg_nums = []

                # Attempt 1: Search by In-Reply-To header
                if clean_id:
                    try:
                        r_status, r_data = imap.search(None, f'HEADER In-Reply-To "{clean_id}"')
                        if r_status == "OK" and r_data[0].split():
                            has_replied = True
                            matched_msg_nums = r_data[0].split()
                    except Exception:
                        pass

                # Attempt 2: Search by Sender email
                if not has_replied:
                    try:
                        r_status, r_data = imap.search(None, f'FROM "{lead_email}"')
                        if r_status == "OK" and r_data[0].split():
                            has_replied = True
                            matched_msg_nums = r_data[0].split()
                    except Exception:
                        pass

                if has_replied:
                    # Check if reply is an opt-out (e.g. "stop" / "unsubscribe")
                    new_status = "REPLIED"
                    opt_out_detected = False

                    if matched_msg_nums:
                        try:
                            latest_num = matched_msg_nums[-1]
                            fetch_status, msg_parts = imap.fetch(latest_num, "(RFC822.TEXT)")
                            if fetch_status == "OK" and msg_parts:
                                for part in msg_parts:
                                    if isinstance(part, tuple) and len(part) > 1:
                                        body_text = part[1].decode(errors="ignore")
                                        if _is_unsubscribe_reply(body_text):
                                            opt_out_detected = True
                                            break
                        except Exception:
                            pass

                    if opt_out_detected:
                        new_status = "UNSUBSCRIBED"
                        log(f"⛔ Opt-out request detected from {lead_email}. Marked as UNSUBSCRIBED.")
                    else:
                        log(f"🟢 Direct reply detected from {lead_email}! Sequence halted & marked as REPLIED.")

                    conn.execute(
                        "UPDATE leads SET status=?, updated_at=? WHERE email=?",
                        (new_status, now_iso, lead_email),
                    )
                    conn.commit()
                    results.append({"email": lead_email, "type": new_status, "detail": "Incoming email matched"})

    finally:
        conn.close()

    return results
