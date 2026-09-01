import asyncio
import logging
import os
import re
from wzgram import Client, filters
from wzgram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)

from utils.db import get_db
from utils.auth import approved_only
from utils.slug_map import get_short_slug, resolve_slug
from utils.logger import get_main_channel

log = logging.getLogger(__name__)


def _extract_series_slug(episode_slug: str) -> str:
    parts = episode_slug.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return episode_slug


async def build_delete_menu(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    db = get_db()
    files = await db.Name.find().to_list(length=200)

    if not files:
        text = "📭 **No downloaded files found in database.**"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Close", callback_data="del_close")]
        ])
        return text, keyboard

    total_size = sum(f.get("file_size", 0) for f in files)
    total_mb = total_size / (1024 * 1024)

    PER_PAGE = 6
    total_pages = (len(files) + PER_PAGE - 1) // PER_PAGE
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * PER_PAGE
    end_idx = start_idx + PER_PAGE
    page_files = files[start_idx:end_idx]

    text = (
        f"🗑 **Manage Downloaded Files**\n\n"
        f"Total Cached Files: **{len(files)}**\n"
        f"Total Size: **{total_mb:.1f} MB**\n"
        f"Page: **{page + 1} / {total_pages}**\n\n"
        f"Tap any file below to delete it completely from MongoDB and storage:"
    )

    keyboard = []
    for f in page_files:
        name = f.get("name", "Unknown")
        size = f.get("file_size", 0)
        size_str = f"{size / (1024 * 1024):.1f} MB" if size else ""
        short_key = await get_short_slug(name)
        
        btn_text = f"🗑 {name[:28]} ({size_str})" if size_str else f"🗑 {name[:32]}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"delfile_{short_key}_{page}")])

    # Pagination buttons
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"delpage_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"delpage_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    # Control buttons
    keyboard.append([InlineKeyboardButton("⚠️ Delete ALL Files", callback_data="delall_confirm")])
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="del_close")])

    return text, InlineKeyboardMarkup(keyboard)


@approved_only
async def delete_command(client: Client, message: Message):
    """Handle /delete command to list and manage downloaded files."""
    text, keyboard = await build_delete_menu(page=0)
    await message.reply_text(text, reply_markup=keyboard)


async def delete_callback_handler(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    db = get_db()

    if data == "del_close":
        try:
            await callback_query.message.delete()
        except Exception:
            await callback_query.answer("Closed")
        return

    if data.startswith("delpage_"):
        page = int(data.split("_")[1])
        text, keyboard = await build_delete_menu(page=page)
        await callback_query.edit_message_text(text, reply_markup=keyboard)
        return

    if data.startswith("delfile_"):
        parts = data.split("_")
        short_key = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0

        slug = await resolve_slug(short_key)
        series_slug = _extract_series_slug(slug)

        # 1. Delete from MongoDB Name collection
        await db.Name.delete_one({"name": slug})

        # 2. Delete from MongoDB catalog
        catalog_doc = await db.catalog.find_one({"series": series_slug})
        if catalog_doc:
            await db.catalog.update_one(
                {"series": series_slug},
                {"$unset": {f"episodes.{slug}": ""}}
            )
            # Check remaining episodes
            updated_doc = await db.catalog.find_one({"series": series_slug})
            if updated_doc:
                eps = updated_doc.get("episodes", {})
                if not eps:
                    # Delete message from channel if recorded
                    msg_id = updated_doc.get("msg_id")
                    main_channel = await get_main_channel()
                    if msg_id and main_channel:
                        try:
                            await client.delete_messages(main_channel, msg_id)
                        except Exception:
                            pass
                    await db.catalog.delete_one({"series": series_slug})

        # 3. Clean local temp file if exists
        for ext in [".mp4", ".mkv", ".ts"]:
            fpath = f"{slug}{ext}"
            if os.path.exists(fpath):
                try:
                    os.unlink(fpath)
                except Exception:
                    pass

        await callback_query.answer(f"✅ Deleted '{slug}' from database!", show_alert=True)
        text, keyboard = await build_delete_menu(page=page)
        await callback_query.edit_message_text(text, reply_markup=keyboard)
        return

    if data == "delall_confirm":
        files_count = await db.Name.count_documents({})
        if files_count == 0:
            await callback_query.answer("No files to delete!", show_alert=True)
            return

        text = (
            f"⚠️ **Confirm Mass Deletion**\n\n"
            f"Are you sure you want to delete ALL **{files_count}** downloaded files?\n"
            f"This action will purge all database records, catalog entries, and cached files!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💥 Yes, Delete All Files", callback_data="delall_do")],
            [InlineKeyboardButton("⬅️ Cancel", callback_data="delpage_0")]
        ])
        await callback_query.edit_message_text(text, reply_markup=keyboard)
        return

    if data == "delall_do":
        # Delete all files from db.Name and db.catalog
        await db.Name.delete_many({})
        await db.catalog.delete_many({})

        await callback_query.answer("💥 All downloaded files purged!", show_alert=True)
        await callback_query.edit_message_text(
            "✅ **All Downloaded Files Deleted!**\n\n"
            "MongoDB collections and file records have been completely cleared."
        )
        return
