import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
SEQUENCES_PATH = BASE_DIR / "sequences.json"

# Load .env if present
load_dotenv(dotenv_path=ENV_PATH)

DEFAULT_SEQUENCES = {
    "1": {
        "step": 1,
        "delay_days": 0,
        "subject": "Quick idea for {company}",
        "body": (
            "Hi {company} team,\n\n"
            "I was looking at {website} and noticed an opportunity where our product "
            "could streamline your workflow.\n\n"
            "Would you be open to a brief 2-minute overview?\n\n"
            "Best,\n{sender_name}\n\n"
            "If this is not relevant, reply 'stop' and I will not follow up."
        )
    },
    "2": {
        "step": 2,
        "delay_days": 3,
        "subject": "Re: Quick idea for {company}",
        "body": (
            "Hi {company} team,\n\n"
            "Following up on my note below in case it got buried. Happy to share a short summary "
            "if this is on your radar.\n\n"
            "Best,\n{sender_name}"
        )
    },
    "3": {
        "step": 3,
        "delay_days": 5,
        "subject": "Re: Quick idea for {company}",
        "body": (
            "Hi {company} team,\n\n"
            "I will close the loop here so I do not crowd your inbox. Feel free to reach back out "
            "if this ever becomes a priority down the road.\n\n"
            "Best,\n{sender_name}"
        )
    }
}


def get_config():
    """Retrieve all configuration settings with reasonable defaults."""
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    return {
        "SMTP_HOST": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "SMTP_PORT": int(os.getenv("SMTP_PORT", "587")),
        "IMAP_HOST": os.getenv("IMAP_HOST", "imap.gmail.com"),
        "IMAP_PORT": int(os.getenv("IMAP_PORT", "993")),
        "SENDER_EMAIL": os.getenv("SENDER_EMAIL", ""),
        "SENDER_NAME": os.getenv("SENDER_NAME", ""),
        "MAIL_APP_PASSWORD": os.getenv("MAIL_APP_PASSWORD", ""),
        "DAILY_SEND_LIMIT": int(os.getenv("DAILY_SEND_LIMIT", "40")),
        "MIN_DELAY_SEC": int(os.getenv("MIN_DELAY_SEC", "180")),
        "MAX_DELAY_SEC": int(os.getenv("MAX_DELAY_SEC", "420")),
        "DB_PATH": str(BASE_DIR / os.getenv("OUTREACH_DB", "outreach.db")),
        "CSV_PATH": str(BASE_DIR / os.getenv("LEADS_CSV", "scraped_leads.csv")),
    }


def save_config(new_values: dict):
    """Save or update environment variables in .env file."""
    current = {}
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    current[k.strip()] = v.strip().strip("'\"")

    current.update({k: str(v) for k, v in new_values.items()})

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("# Pitch Automator Environment Configuration\n")
        for k, v in current.items():
            f.write(f'{k}="{v}"\n')

    # Reload into runtime
    load_dotenv(dotenv_path=ENV_PATH, override=True)


def get_sequences(db_path: Optional[str] = None, *args, **kwargs) -> Dict[str, Any]:
    """Load sequences with priority: Database -> sequences.json -> DEFAULT_SEQUENCES."""
    # 1. Try loading from SQLite database first
    active_db = db_path or str(BASE_DIR / os.getenv("OUTREACH_DB", "outreach.db"))
    if Path(active_db).exists():
        try:
            from db import get_db_sequences
            db_seqs = get_db_sequences(active_db)
            if db_seqs and len(db_seqs) > 0:
                return db_seqs
        except Exception:
            pass

    # 2. Try loading from sequences.json
    if SEQUENCES_PATH.exists():
        try:
            with open(SEQUENCES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and isinstance(data, dict):
                    return data
        except Exception:
            pass

    # 3. Fallback to default sequences and write to JSON
    save_sequences(DEFAULT_SEQUENCES, db_path=active_db)
    return DEFAULT_SEQUENCES


def save_sequences(sequences: Dict[str, Any], db_path: Optional[str] = None, *args, **kwargs):
    """Save updated sequence templates to both sequences.json and SQLite database."""
    # 1. Save to JSON
    try:
        with open(SEQUENCES_PATH, "w", encoding="utf-8") as f:
            json.dump(sequences, f, indent=2)
    except Exception:
        pass

    # 2. Save to SQLite database
    active_db = db_path or str(BASE_DIR / os.getenv("OUTREACH_DB", "outreach.db"))
    try:
        from db import save_db_sequences
        save_db_sequences(active_db, sequences)
    except Exception:
        pass

