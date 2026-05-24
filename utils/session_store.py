"""
Session string storage in MongoDB.

Saves the Pyrogram session string so you don't need to re-login
after redeploys. The env var SESSION_STRING always takes priority.
"""

import logging
from utils.db import get_db

log = logging.getLogger(__name__)

SESSION_KEY = "session_string"


async def load_session_string() -> str | None:
    """Load session string from MongoDB config."""
    try:
        db = get_db()
        doc = await db.config.find_one({"key": SESSION_KEY})
        if doc and doc.get("value"):
            log.info("Session string loaded from database")
            return doc["value"]
    except Exception as e:
        log.warning("Failed to load session string from DB: %s", e)
    return None


async def save_session_string(session_string: str):
    """Save session string to MongoDB config."""
    try:
        db = get_db()
        await db.config.update_one(
            {"key": SESSION_KEY},
            {"$set": {"key": SESSION_KEY, "value": session_string}},
            upsert=True,
        )
        log.info("Session string saved to database")
    except Exception as e:
        log.warning("Failed to save session string to DB: %s", e)
