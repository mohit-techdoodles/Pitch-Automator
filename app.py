import streamlit as st
import pandas as pd
import json
import io
from pathlib import Path
from datetime import datetime, timezone
import plotly.express as px

from config import get_config, save_config, get_sequences, save_sequences, BASE_DIR
from db import (
    init_db,
    get_lead_stats,
    get_daily_sent_count,
    get_leads,
    get_due_leads,
    import_leads_from_rows,
    update_lead_status,
    get_recent_send_logs,
)
from inbox_sync import test_imap_connection, sync_inbox_events
from dispatcher import (
    test_smtp_connection,
    send_test_email,
    run_pitch_dispatch,
    remaining_daily_quota,
    build_message,
)

# Set page configuration
st.set_page_config(
    page_title="Pitch Automator - Cold Outreach Engine",
    page_icon="📨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for polished interface
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.1rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 0.95rem;
        color: #64748B;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 16px 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .metric-title {
        font-size: 0.85rem;
        font-weight: 600;
        color: #64748B;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .metric-value {
        font-size: 1.85rem;
        font-weight: 700;
        color: #0F172A;
        margin-top: 4px;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 18px;
        font-weight: 600;
        border-radius: 6px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Load configuration and initialize database
cfg = get_config()
db_path = cfg["DB_PATH"]
init_db(db_path)
sequences = get_sequences()

# Sidebar: Identity & System Summary
with st.sidebar:
    st.markdown("### 📨 Pitch Automator")
    st.caption("Plain-Text Multi-Step Outreach Worker")
    st.divider()

    # Daily Quota Monitor
    daily_limit = cfg.get("DAILY_SEND_LIMIT", 40)
    sent_today = get_daily_sent_count(db_path)
    quota_left = max(0, daily_limit - sent_today)
    progress_val = min(1.0, sent_today / daily_limit) if daily_limit > 0 else 0.0

    st.markdown(f"**Today's Send Quota (UTC)**")
    st.progress(progress_val)
    st.markdown(f"**{sent_today}** / {daily_limit} sent today (`{quota_left}` remaining)")

    st.divider()
    st.markdown("**Quick Mailbox Check**")
    sender = cfg.get("SENDER_EMAIL")
    if sender:
        st.write(f"👤 `{sender}`")
    else:
        st.warning("Sender email not configured yet.")

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("Test SMTP", use_container_width=True):
            with st.spinner("Checking SMTP..."):
                ok, msg = test_smtp_connection(cfg)
                if ok:
                    st.success("SMTP OK")
                else:
                    st.error(msg)
    with col_btn2:
        if st.button("Test IMAP", use_container_width=True):
            with st.spinner("Checking IMAP..."):
                ok, msg = test_imap_connection(cfg)
                if ok:
                    st.success("IMAP OK")
                else:
                    st.error(msg)

    st.divider()
    st.caption("RFC 5322 Plain-Text | IMAP In-Reply-To Threading | SQLite Storage")

# Main Header
st.markdown('<div class="main-header">Python Pitch Automator</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Self-hosted cold outreach engine with automatic reply/bounce sync and deliverability guardrails.</div>', unsafe_allow_html=True)

# Tabs
tab_overview, tab_leads, tab_sequences, tab_runner, tab_logs, tab_settings = st.tabs([
    "📊 Overview",
    "📥 Lead Ingestion",
    "✉️ Pitch Sequences",
    "🚀 Outreach Runner",
    "📜 Send Logs",
    "⚙️ Settings",
])

# ==========================================
# TAB 1: OVERVIEW & METRICS
# ==========================================
with tab_overview:
    stats = get_lead_stats(db_path)

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        st.metric("Total Leads", stats["TOTAL"])
    with col2:
        st.metric("Pending", stats["PENDING"])
    with col3:
        st.metric("In Sequence", stats["IN_SEQUENCE"])
    with col4:
        st.metric("Replied 🎉", stats["REPLIED"])
    with col5:
        st.metric("Bounced 🔴", stats["BOUNCED"])
    with col6:
        st.metric("Completed", stats["COMPLETED"])

    st.write("")
    chart_col, due_col = st.columns([1, 1])

    with chart_col:
        st.subheader("Lead Status Breakdown")
        status_data = {
            "Status": ["Pending", "In Sequence", "Replied", "Bounced", "Completed", "Unsubscribed"],
            "Count": [
                stats["PENDING"],
                stats["IN_SEQUENCE"],
                stats["REPLIED"],
                stats["BOUNCED"],
                stats["COMPLETED"],
                stats["UNSUBSCRIBED"],
            ]
        }
        df_status = pd.DataFrame(status_data)
        df_status = df_status[df_status["Count"] > 0]

        if not df_status.empty:
            color_map = {
                "Pending": "#94A3B8",
                "In Sequence": "#3B82F6",
                "Replied": "#10B981",
                "Bounced": "#EF4444",
                "Completed": "#6366F1",
                "Unsubscribed": "#F59E0B",
            }
            fig = px.pie(
                df_status,
                names="Status",
                values="Count",
                hole=0.45,
                color="Status",
                color_discrete_map=color_map,
            )
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No leads in database yet. Ingest leads via the 'Lead Ingestion' tab.")

    with due_col:
        st.subheader("Upcoming / Due Pitches")
        due_leads = get_due_leads(db_path, max_step=len(sequences), limit=5)
        if due_leads:
            due_display = []
            for d in due_leads:
                next_step = d["current_step"] + 1
                due_display.append({
                    "Email": d["email"],
                    "Company": d["company"],
                    "Next Step": f"Step {next_step}",
                    "Status": d["status"],
                    "Due At": d["next_send_at"] or "Immediate",
                })
            st.dataframe(pd.DataFrame(due_display), use_container_width=True, hide_index=True)
            st.caption(f"Showing up to 5 of {len(get_due_leads(db_path, max_step=len(sequences)))} due leads. Run them in the **Outreach Runner** tab.")
        else:
            st.success("✅ No pitches are currently pending or due right now!")

# ==========================================
# TAB 2: LEAD INGESTION & MANAGEMENT
# ==========================================
with tab_leads:
    st.subheader("1. Ingest Scraped Leads from CSV")
    st.markdown(
        "Upload a CSV export containing verified leads. Required columns: `email`, `company`. "
        "Optional columns: `website`, `country`. Existing leads are **never overwritten**."
    )

    uploaded_file = st.file_uploader("Upload Leads CSV", type=["csv"])
    if uploaded_file is not None:
        try:
            raw_df = pd.read_csv(uploaded_file)
            st.write(f"Previewing `{len(raw_df)}` rows from uploaded file:")
            st.dataframe(raw_df.head(5), use_container_width=True)

            # Column mapping check
            cols = [c.lower().strip() for c in raw_df.columns]
            has_email = any("email" in c or "mail" in c for c in cols)
            has_company = any("company" in c or "name" in c or "hotel" in c for c in cols)

            if not has_email:
                st.error("Uploaded CSV must have an email column (e.g. 'Direct Email', 'email', 'contact_email').")
            else:
                # Detect if this is from Lead Scrapper
                is_hotel_format = any("hotel" in c for c in cols)
                if is_hotel_format:
                    st.info("🏨 **Lead Scrapper Hotel Format Detected:** Automatically mapping `Hotel Name` ➔ Company, `Direct Email` ➔ Email, and `Official Website` ➔ Website.")

                if st.button("📥 Import Leads into Outreach Database", type="primary"):
                    records = raw_df.to_dict(orient="records")
                    inserted, ignored = import_leads_from_rows(db_path, records)
                    st.success(f"🎉 Successfully imported {inserted} new lead(s) with valid emails! ({ignored} skipped due to duplicates or unlisted emails)")
                    st.rerun()
        except Exception as e:
            st.error(f"Error reading CSV: {e}")

    st.divider()
    st.subheader("2. Lead Directory & Status Management")

    col_filter1, col_filter2, col_export = st.columns([2, 3, 2])
    with col_filter1:
        status_filter = st.selectbox(
            "Filter by Status",
            ["ALL", "PENDING", "IN_SEQUENCE", "REPLIED", "BOUNCED", "COMPLETED", "UNSUBSCRIBED"]
        )
    with col_filter2:
        search_query = st.text_input("Search (email, company, website)", placeholder="Search keyword...")
    with col_export:
        st.write("")
        st.write("")
        all_leads = get_leads(db_path, status="ALL", limit=10000)
        if all_leads:
            csv_buffer = io.StringIO()
            pd.DataFrame(all_leads).to_csv(csv_buffer, index=False)
            st.download_button(
                "Export Database CSV",
                data=csv_buffer.getvalue(),
                file_name="pitch_automator_leads.csv",
                mime="text/csv",
                use_container_width=True,
            )

    leads_data = get_leads(db_path, status=status_filter, search=search_query, limit=500)
    if leads_data:
        df_leads = pd.DataFrame(leads_data)
        display_cols = ["email", "company", "country", "website", "current_step", "status", "next_send_at", "updated_at"]
        st.dataframe(df_leads[display_cols], use_container_width=True, hide_index=True)

        st.caption("Manual override: change status of an existing lead")
        col_ov1, col_ov2, col_ov3 = st.columns([3, 2, 2])
        with col_ov1:
            lead_to_edit = st.selectbox("Select Lead Email", [l["email"] for l in leads_data])
        with col_ov2:
            new_st = st.selectbox("New Status", ["UNSUBSCRIBED", "REPLIED", "PENDING", "BOUNCED", "COMPLETED"])
        with col_ov3:
            st.write("")
            st.write("")
            if st.button("Apply Status Override"):
                update_lead_status(db_path, lead_to_edit, new_st)
                st.success(f"Updated {lead_to_edit} -> {new_st}")
                st.rerun()
    else:
        st.info("No leads found matching current criteria.")

# ==========================================
# TAB 3: PITCH SEQUENCES & TEMPLATES
# ==========================================
with tab_sequences:
    st.subheader("Email Sequence Builder")
    st.markdown(
        "Craft your multi-step plain-text pitch. Available variables: `{company}`, `{website}`, `{country}`, `{sender_name}`, `{sender_email}`."
    )

    step_tabs = st.tabs([f"Step {k}" for k in sorted(sequences.keys(), key=lambda x: int(x))])

    updated_sequences = dict(sequences)

    for idx, (step_key, step_data) in enumerate(sorted(sequences.items(), key=lambda x: int(x[0]))):
        with step_tabs[idx]:
            col_cfg1, col_cfg2 = st.columns([2, 5])
            with col_cfg1:
                delay = st.number_input(
                    f"Delay Days before Step {step_key}",
                    min_value=0,
                    max_value=30,
                    value=int(step_data.get("delay_days", 0)),
                    key=f"delay_{step_key}",
                    help="Days to wait after the previous email was sent (0 for initial pitch)."
                )
            with col_cfg2:
                subject = st.text_input(
                    f"Subject Line (Step {step_key})",
                    value=step_data.get("subject", ""),
                    key=f"sub_{step_key}",
                )
                if "{company}" in subject:
                    st.caption("🟢 High Deliverability: Subject contains `{company}` personalization.")
                else:
                    st.warning("⚠️ Deliverability Warning: Subject is missing `{company}`. Personalizing with `{company}` prevents messages from being flagged as spam.")

            body = st.text_area(
                f"Plain-Text Body (Step {step_key})",
                value=step_data.get("body", ""),
                height=180,
                key=f"body_{step_key}",
            )

            updated_sequences[step_key] = {
                "step": int(step_key),
                "delay_days": delay,
                "subject": subject,
                "body": body,
            }

            # Live Preview with dummy data demonstrating the Intelligent Sanitizer
            st.markdown("##### 👁️ Live Preview for Sample Lead:")
            sample_lead = {
                "company": "Acme Innovations Pvt. Ltd.",
                "website": "https://acme.com",
                "country": "US",
                "email": "contact@acme.com",
            }
            preview_msg, _ = build_message(
                lead=sample_lead,
                step_num=int(step_key),
                step_config=updated_sequences[step_key],
                sender_email=cfg.get("SENDER_EMAIL") or "outreach@yourdomain.com",
                sender_name=cfg.get("SENDER_NAME") or "Alex",
            )
            preview_box = f"**Subject:** {preview_msg['Subject']}\n\n```\n{preview_msg.get_content()}\n```"
            st.markdown(preview_box)
            st.caption("💡 *Intelligent Sanitizer automatically cleans raw legal suffixes (e.g. 'Acme Innovations Pvt. Ltd.' ➔ 'Acme Innovations') so emails appear authentic and 1-on-1.*")

    st.write("")
    if st.button("💾 Save All Sequence Templates", type="primary"):
        save_sequences(updated_sequences)
        st.success("Sequence templates saved successfully!")
        st.rerun()

# ==========================================
# TAB 4: OUTREACH RUNNER (DISPATCH & SYNC)
# ==========================================
with tab_runner:
    st.subheader("1. Inbound Inbox Sync (IMAP)")
    st.markdown("Polls your inbox for replies or mailer-daemon bounce notices to automatically halt active sequences.")

    col_sync1, col_sync2 = st.columns([1, 2])
    with col_sync1:
        if st.button("🔄 Sync Inbox Events Now", use_container_width=True):
            with st.spinner("Connecting to IMAP & scanning inbox..."):
                sync_logs = []
                res = sync_inbox_events(db_path, cfg, log_callback=lambda m: sync_logs.append(m))
                st.session_state["sync_logs"] = sync_logs
                st.session_state["sync_results"] = res
                st.rerun()

    if "sync_logs" in st.session_state:
        st.write("IMAP Sync Execution Log:")
        st.code("\n".join(st.session_state["sync_logs"]), language="text")
        if st.session_state.get("sync_results"):
            st.dataframe(pd.DataFrame(st.session_state["sync_results"]), use_container_width=True)

    st.divider()
    st.subheader("2. Outbound Pitch Dispatcher (SMTP)")

    total_stats = get_lead_stats(db_path)
    total_leads_count = total_stats["TOTAL"]

    col_toggle1, col_toggle2 = st.columns([1, 1])
    with col_toggle1:
        ignore_delay_toggle = st.checkbox(
            "⚡ Send Immediately (Ignore scheduled delay days)",
            value=False,
            help="Check this if you want to send the next follow-up right away without waiting for the scheduled 3-5 day delay.",
        )

    due_list = get_due_leads(db_path, max_step=len(sequences), ignore_delay=ignore_delay_toggle)
    quota_avail = remaining_daily_quota(db_path, int(cfg.get("DAILY_SEND_LIMIT", 40)))

    col_q1, col_q2 = st.columns(2)
    with col_q1:
        st.info(f"📋 **{len(due_list)}** lead(s) currently ready to receive an email.")
    with col_q2:
        st.info(f"⏳ **{quota_avail}** email(s) remaining in today's daily quota.")

    # Clear contextual banners explaining exact status
    if total_leads_count == 0:
        st.warning("⚠️ **No Leads in Database:** The button below is disabled because no leads have been imported yet. Please go to the **📥 Lead Ingestion** tab and upload your CSV first.")
    elif quota_avail <= 0:
        st.error("⛔ **Daily Quota Reached:** Today's send limit has been reached. You can increase the Daily Send Limit in the **⚙️ Settings** tab.")
    elif len(due_list) == 0:
        st.info("ℹ️ **No Leads Due Right Now:** All leads in your database have already received their current step and are waiting for their scheduled follow-up delay (e.g., Step 2 in 3 days). Check **'Send Immediately'** above if you want to bypass the wait time.")

    col_run_opt1, col_run_opt2, col_run_opt3 = st.columns(3)
    with col_run_opt1:
        dry_run_mode = st.checkbox("🧪 Dry Run (Simulate without sending)", value=True, help="Logs the outgoing pitches without actually connecting to SMTP server.")
    with col_run_opt2:
        max_batch = st.number_input("Max leads to send in this batch", min_value=1, max_value=max(1, quota_avail), value=min(5, max(1, quota_avail)))
    with col_run_opt3:
        bypass_delay = st.checkbox("⚡ Fast Mode (Skip 3-7m delay)", value=True, help="Skips the sleep window between emails (useful for testing or small initial batches).")

    is_dispatch_disabled = (quota_avail <= 0 or len(due_list) == 0)

    if st.button("🚀 Start Outreach Dispatch", type="primary", disabled=is_dispatch_disabled):
        log_box = st.empty()
        logs_accum = []

        def log_stream(m: str):
            logs_accum.append(m)
            log_box.code("\n".join(logs_accum), language="text")

        with st.spinner("Processing outreach batch..."):
            result = run_pitch_dispatch(
                db_path=db_path,
                config=cfg,
                sequences=sequences,
                max_to_send=int(max_batch),
                dry_run=dry_run_mode,
                sleep_between=not bypass_delay,
                ignore_delay=ignore_delay_toggle,
                log_callback=log_stream,
            )
            st.success(f"Batch completed! Processed: {result.get('sent', 0)} email(s).")

    st.divider()
    st.subheader("3. Single Email Deliverability Test")
    st.caption("Send a 1-off test message to verify STARTTLS, plain-text headers, and inbox placement.")

    col_test1, col_test2 = st.columns([3, 1])
    with col_test1:
        test_email_addr = st.text_input("Your Test Recipient Email", placeholder="yourname@gmail.com")
    with col_test2:
        st.write("")
        st.write("")
        if st.button("Send Test Email", use_container_width=True):
            if not test_email_addr or "@" not in test_email_addr:
                st.error("Please provide a valid recipient email.")
            else:
                with st.spinner("Sending test pitch..."):
                    ok, res = send_test_email(cfg, test_email_addr)
                    if ok:
                        st.success(res)
                    else:
                        st.error(res)

# ==========================================
# TAB 5: AUDIT SEND LOGS
# ==========================================
with tab_logs:
    st.subheader("Outreach Audit Log (`send_log`)")
    st.markdown("Every sent message is committed immediately upon SMTP acceptance to guarantee crashes or restarts never trigger duplicate emails.")

    logs = get_recent_send_logs(db_path, limit=200)
    if logs:
        df_logs = pd.DataFrame(logs)
        st.dataframe(df_logs, use_container_width=True, hide_index=True)

        csv_logs = io.StringIO()
        df_logs.to_csv(csv_logs, index=False)
        st.download_button(
            "Download Send Logs CSV",
            data=csv_logs.getvalue(),
            file_name="outreach_send_log.csv",
            mime="text/csv",
        )
    else:
        st.info("No emails have been dispatched yet. Outgoing emails will appear here automatically.")

# ==========================================
# TAB 6: SETTINGS & MAILBOX CONFIGURATION
# ==========================================
with tab_settings:
    st.subheader("Mailbox & Operational Settings")
    st.markdown(
        "Credentials are read from and written to `.env`. "
        "Always use a **secondary warmed domain** and an **App Password** (never your personal login password)."
    )

    with st.form("settings_form"):
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            st.markdown("##### 📤 Outbound SMTP Settings")
            smtp_h = st.text_input("SMTP Host", value=cfg.get("SMTP_HOST", "smtp.gmail.com"))
            smtp_p = st.number_input("SMTP Port (587 STARTTLS, 465 SSL)", min_value=1, max_value=65535, value=int(cfg.get("SMTP_PORT", 587)))

        with col_s2:
            st.markdown("##### 📥 Inbound IMAP Settings")
            imap_h = st.text_input("IMAP Host", value=cfg.get("IMAP_HOST", "imap.gmail.com"))
            imap_p = st.number_input("IMAP Port (993 SSL)", min_value=1, max_value=65535, value=int(cfg.get("IMAP_PORT", 993)))

        st.markdown("##### 👤 Sender Identity & Credentials")
        col_c1, col_c2, col_c3 = st.columns([2, 2, 3])
        with col_c1:
            sender_em = st.text_input("Sender Email Address", value=cfg.get("SENDER_EMAIL", ""))
        with col_c2:
            sender_nm = st.text_input("Sender Display Name", value=cfg.get("SENDER_NAME", ""))
        with col_c3:
            app_pw = st.text_input("Mailbox App Password", value=cfg.get("MAIL_APP_PASSWORD", ""), type="password")

        st.markdown("##### 🛡️ Deliverability & Pacing Guardrails")
        col_g1, col_g2, col_g3 = st.columns(3)
        with col_g1:
            d_limit = st.number_input("Daily Send Limit (per UTC day)", min_value=1, max_value=200, value=int(cfg.get("DAILY_SEND_LIMIT", 40)), help="Recommended max 40-50 per warmed mailbox.")
        with col_g2:
            min_d = st.number_input("Min Delay Between Sends (seconds)", min_value=5, max_value=600, value=int(cfg.get("MIN_DELAY_SEC", 180)))
        with col_g3:
            max_d = st.number_input("Max Delay Between Sends (seconds)", min_value=10, max_value=1200, value=int(cfg.get("MAX_DELAY_SEC", 420)))

        save_btn = st.form_submit_button("💾 Save Settings to .env", type="primary")
        if save_btn:
            new_settings = {
                "SMTP_HOST": smtp_h.strip(),
                "SMTP_PORT": int(smtp_p),
                "IMAP_HOST": imap_h.strip(),
                "IMAP_PORT": int(imap_p),
                "SENDER_EMAIL": sender_em.strip(),
                "SENDER_NAME": sender_nm.strip(),
                "MAIL_APP_PASSWORD": app_pw.strip(),
                "DAILY_SEND_LIMIT": int(d_limit),
                "MIN_DELAY_SEC": int(min_d),
                "MAX_DELAY_SEC": int(max_d),
            }
            save_config(new_settings)
            st.success("✅ Configuration successfully saved to `.env`!")
            st.rerun()

    st.divider()
    st.markdown("##### 📁 Local Paths")
    st.text(f"Database: {cfg.get('DB_PATH')}")
    st.text(f"Base Directory: {BASE_DIR}")
