"""Admin commands for the shared Oppai.stream account session."""

import asyncio
import logging

from wzgram import Client
from wzgram.types import Message

from api.hanime_api import HanimeAPI
from utils.auth import admin_only
from utils.db import get_db

log = logging.getLogger(__name__)
oppai_api = HanimeAPI()


async def _delete_credentials(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        log.warning("Could not delete Oppai credential message")


@admin_only
async def oppai_login_command(client: Client, message: Message):
    """Log in the shared bot session: /oppai_login <username> <password>."""
    parts = message.text.split(maxsplit=2)
    await _delete_credentials(message)
    if len(parts) != 3:
        await client.send_message(
            message.chat.id,
            "**Usage:** `/oppai_login <username-or-email> <password>`\n\n"
            "Send it only in this private chat. The command message is deleted immediately.",
        )
        return

    username, password = parts[1:]
    try:
        success, result, cookies = await asyncio.to_thread(oppai_api.login_oppai, username, password)
    except Exception:
        log.exception("Oppai login request failed")
        await client.send_message(message.chat.id, "Oppai login is currently unavailable.")
        return

    if not success:
        await client.send_message(message.chat.id, f"❌ {result}")
        return

    await get_db().oppai_auth.replace_one(
        {"_id": "session"},
        {"_id": "session", "cookies": cookies},
        upsert=True,
    )
    await client.send_message(message.chat.id, "✅ Oppai login saved. 4K episodes requiring an account can now be resolved.")


@admin_only
async def oppai_logout_command(client: Client, message: Message):
    oppai_api.logout_oppai()
    await get_db().oppai_auth.delete_one({"_id": "session"})
    await message.reply_text("✅ Oppai session removed.")


@admin_only
async def oppai_status_command(client: Client, message: Message):
    status = "logged in" if oppai_api.oppai_logged_in() else "not logged in"
    await message.reply_text(f"Oppai session: **{status}**")
