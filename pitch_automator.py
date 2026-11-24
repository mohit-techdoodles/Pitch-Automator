#!/usr/bin/env python3
"""
Python Pitch Automator
Plain-Text Multi-Step Cold Email Outreach Worker with IMAP Reply Detection
Runtime: Python 3.10+ (Standard Library) | Storage: SQLite | Transport: SMTP / IMAP
"""

import argparse
import sys
from pathlib import Path
from config import get_config, get_sequences
from db import init_db, import_csv_file
from inbox_sync import sync_inbox_events
from dispatcher import run_pitch_dispatch


def main():
    parser = argparse.ArgumentParser(description="Python Pitch Automator CLI & Cron Worker")
    parser.add_argument("--dry-run", action="store_true", help="Simulate sends without connecting to live SMTP")
    parser.add_argument("--sync-only", action="store_true", help="Only run IMAP reply and bounce checks")
    parser.add_argument("--send-only", action="store_true", help="Only run SMTP dispatch without IMAP check")
    parser.add_argument("--max", type=int, default=None, help="Maximum emails to send in this run")
    parser.add_argument("--no-sleep", action="store_true", help="Disable sleep between sends (for testing)")
    parser.add_argument("--csv", type=str, default=None, help="Custom CSV path to ingest")

    args = parser.parse_args()

    config = get_config()
    db_path = config["DB_PATH"]
    csv_path = args.csv or config["CSV_PATH"]
    sequences = get_sequences()

    init_db(db_path)
    print("=" * 60)
    print("🚀 Python Pitch Automator - Starting Run")
    print("=" * 60)

    # 1. Ingest Phase
    if not args.sync_only:
        if Path(csv_path).exists():
            print(f"📥 Phase 1: Ingesting leads from {csv_path}...")
            inserted, ignored = import_csv_file(db_path, csv_path)
            print(f"   Inserted {inserted} new lead(s) (ignored {ignored} existing).")
        else:
            print(f"ℹ️ Ingest: CSV file '{csv_path}' not found. Continuing with existing database leads.")

    # 2. Sync Phase (IMAP)
    if not args.send_only:
        print("\n📬 Phase 2: Syncing inbox events via IMAP...")
        results = sync_inbox_events(db_path, config, log_callback=print)
        print(f"   Processed {len(results)} new inbox event(s).")

    # 3. Dispatch Phase (SMTP)
    if not args.sync_only:
        print("\n📤 Phase 3: Dispatching due pitches via SMTP...")
        dispatch_results = run_pitch_dispatch(
            db_path=db_path,
            config=config,
            sequences=sequences,
            max_to_send=args.max,
            dry_run=args.dry_run,
            sleep_between=not args.no_sleep,
            log_callback=print,
        )
        print(f"   Dispatched {dispatch_results.get('sent', 0)} email(s).")

    print("\n💾 Phase 4: All changes persisted to outreach.db.")
    print("🏁 Execution complete.\n")


if __name__ == "__main__":
    main()
