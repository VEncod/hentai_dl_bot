"""
Centralized MongoDB connection singleton.

Usage:
    from utils.db import get_db, init_db

    # Call once at startup:
    await init_db(mongo_url)

    # Then anywhere:
    db = get_db()
    doc = await db.admins.find_one({"user_id": 123})
"""

import logging
from motor.motor_asyncio import AsyncIOMotorClient

log = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_db = None
_hindi_db = None


async def init_db(mongo_url: str, db_name: str = "MangaDb", hindi_db_name: str = "HindiDb"):
    """Initialize the global MongoDB connections."""
    global _client, _db, _hindi_db
    _client = AsyncIOMotorClient(mongo_url)
    _db = _client[db_name]
    _hindi_db = _client[hindi_db_name]
    # Verify connection
    await _client.admin.command("ping")
    log.info("MongoDB connected: %s (hanime) + %s (hindi)", db_name, hindi_db_name)


def get_db():
    """Return the MangaDb database instance (hanime). Must call init_db() first."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


def get_hindi_db():
    """Return the HindiDb database instance. Must call init_db() first."""
    if _hindi_db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _hindi_db


def get_client() -> AsyncIOMotorClient:
    """Return the raw MongoClient."""
    if _client is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _client
