# Python Pitch Automator

**Plain-Text Multi-Step Cold Email Outreach Worker with IMAP Reply Detection & Streamlit Dashboard**

A self-hosted, anti-spam outreach engine designed according to strict RFC 5322 plain-text standards. It reads verified leads, builds follow-ups threaded into the same conversation, and automatically halts sequences when prospects reply or bounce via IMAP.

---

## 🌟 Key Features

- **Primary Inbox Placement:** 100% plain-text emails. Zero open-tracking pixels, zero HTML templates, zero suspicious redirect URLs.
- **Smart Conversation Threading:** Follow-up pitches carry RFC 5322 `In-Reply-To` and `References` headers matching the original `Message-ID`, so follow-ups stack inside the original thread.
- **Real-Time Reply & Bounce Detection:** Polls your inbox via IMAP before sending batches. Automatically halts sequences and updates lead state to `REPLIED`, `BOUNCED`, or `UNSUBSCRIBED`.
- **Streamlit Web Dashboard:** Upload CSVs, customize sequences, test connections, monitor daily quotas, and trigger runs visually.
- **CLI & Cron Automation:** Run autonomously in the background on your machine or VPS with Linux `flock` overlap protection.
- **Strict Rate Limiting & Sleep Pacing:** Enforces daily send caps (default 40/day) and randomized delays (180–420 seconds) between sends.
- **Crash-Proof Persistence:** SQLite database commits each send immediately upon SMTP acceptance, preventing duplicate sends on crashes or restarts.

---

## 📁 Project Structure

```text
Pitch Automator/
├── app.py                # Streamlit Web Dashboard (UI)
├── pitch_automator.py    # Standalone CLI / Cron Worker (Backend)
├── config.py             # Environment & sequences configuration manager
├── db.py                 # SQLite schema, lead ingestion, and query logic
├── inbox_sync.py         # IMAP reply and mailer-daemon bounce sync
├── dispatcher.py         # SMTP RFC 5322 builder, sender, and delay throttling
├── sequences.json        # Pitch sequence templates (Steps 1, 2, 3)
├── sample_leads.csv      # Example lead CSV file
├── requirements.txt      # Python dependencies (streamlit, pandas, plotly, python-dotenv)
├── .env.example          # Sample environment variables
└── README.md             # Documentation
```

---

## 🚀 Quick Start

### 1. Install Dependencies
```bash
cd "/Users/mohitpatel/Desktop/python/Pitch Automator"
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
Copy `.env.example` to `.env` or configure directly inside the Streamlit **Settings** tab:
```bash
cp .env.example .env
```

Edit your credentials:
```env
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
IMAP_HOST="imap.gmail.com"
IMAP_PORT="993"
SENDER_EMAIL="outreach@yourdomain.com"
SENDER_NAME="Your Name"
MAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
DAILY_SEND_LIMIT="40"
MIN_DELAY_SEC="180"
MAX_DELAY_SEC="420"
```

> **Note for Gmail / Google Workspace:** Generate a 16-character **App Password** from your Google Account (Security → 2-Step Verification → App passwords). Never use your personal login password.

### 3. Launch the Streamlit Web Dashboard
```bash
streamlit run app.py
```
Open your browser at `http://localhost:8501`.

---

## 🖥️ Using the Web Dashboard

1. **Overview:** View campaign metrics, daily quota gauge, lead breakdown, and upcoming pitches.
2. **Lead Ingestion:** Drag-and-drop your lead CSV (e.g. `sample_leads.csv` or an export from your scraper).
3. **Pitch Sequences:** Customize Step 1, Step 2, and Step 3 subjects and body copy with dynamic placeholders:
   - `{company}`
   - `{website}`
   - `{country}`
   - `{sender_name}`
4. **Outreach Runner:**
   - Click **Sync Inbox Events Now** to fetch new replies/bounces via IMAP.
   - Run a **Dry Run** to simulate message building and threading.
   - Click **Start Outreach Dispatch** to send due pitches.
   - Test single-email deliverability directly to your inbox.
5. **Send Logs:** Audit log showing every dispatched message with UTC timestamp and Message-ID.
6. **Settings:** Test SMTP and IMAP connections with instant diagnostic feedback.

---

## 🤖 Running the CLI & Cron Job

You can also run the worker completely headless without the UI:

### Manual CLI Run
```bash
# Ingest CSV, sync IMAP, and send due pitches
python3 pitch_automator.py

# Test run without sending actual emails (Dry Run)
python3 pitch_automator.py --dry-run --no-sleep

# Only check IMAP for replies and bounces
python3 pitch_automator.py --sync-only

# Ingest a custom leads CSV
python3 pitch_automator.py --csv /path/to/leads.csv
```

### Automated Scheduling (Cron on Linux/macOS)
To run every 30 minutes on weekdays between 9 AM and 5 PM with overlap protection:
```bash
*/30 9-17 * * 1-5 /usr/bin/flock -n /tmp/pitch_automator.lock \
/usr/bin/env bash -c 'cd "/Users/mohitpatel/Desktop/python/Pitch Automator" && python3 pitch_automator.py >> outreach.log 2>&1'
```

---

## 🛡️ Deliverability & Safe Outreach Checklist

1. **Secondary Domain:** Always send cold emails from a secondary domain (e.g., `trycompany.com` instead of `company.com`).
2. **DNS Records:** Ensure your DNS has valid **SPF**, **DKIM**, and **DMARC** records configured.
3. **Mailbox Warmup:** Warm up any fresh mailbox for 2–3 weeks before sending cold outreach.
4. **Daily Caps:** Keep `DAILY_SEND_LIMIT` under 40–50 emails per mailbox per day.
5. **Pre-Send Verification:** Verify scraped emails using MX/SMTP checks before adding them to keep hard bounce rates under 2%.
6. **Opt-Out Compliance:** Always leave an unsubscribe instruction (e.g., *“If not interested, reply 'stop' and I will not follow up”*). The automator automatically detects replies containing "stop" or "unsubscribe" and marks them `UNSUBSCRIBED`.
