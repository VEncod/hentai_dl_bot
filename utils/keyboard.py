"""Custom Reply Keyboard and user search source preferences."""

import logging
from wzgram.types import KeyboardButton, ReplyKeyboardMarkup
from utils.db import get_db

log = logging.getLogger(__name__)

# Default search source: 'both' | 'htv' | 'oppai'
DEFAULT_SOURCE = "both"

BUTTON_HENTAI_TV = "🌐 Hentai.tv (Default)"
BUTTON_OPPAI_STREAM = "✨ Oppai.stream (4K)"
BUTTON_BOTH_SOURCES = "🔍 Search Both Sources"
BUTTON_SERIES_ARCHIVE = "📂 Series Archive"


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Return persistent Custom Reply Keyboard for search mode selection."""
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(BUTTON_HENTAI_TV),
                KeyboardButton(BUTTON_OPPAI_STREAM),
            ],
            [
                KeyboardButton(BUTTON_BOTH_SOURCES),
                KeyboardButton(BUTTON_SERIES_ARCHIVE),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


async def get_user_search_source(user_id: int) -> str:
    """Get the active search source preference for a user ('both', 'htv', 'oppai')."""
    try:
        db = get_db()
        doc = await db.user_preferences.find_one({"user_id": user_id})
        if doc and doc.get("search_source"):
            return doc["search_source"]
    except Exception as e:
        log.warning("Failed to get search source for user %s: %s", user_id, e)
    return DEFAULT_SOURCE


async def set_user_search_source(user_id: int, source: str) -> None:
    """Set the active search source preference for a user ('both', 'htv', 'oppai')."""
    try:
        db = get_db()
        await db.user_preferences.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "search_source": source}},
            upsert=True,
        )
    except Exception as e:
        log.warning("Failed to save search source for user %s: %s", user_id, e)
