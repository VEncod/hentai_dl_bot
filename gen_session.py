"""
Run this ONCE locally to generate your SESSION_STRING.

Usage:
    pip install pyrogram tgcrypto
    python gen_session.py

Then copy the printed string and add it to Railway as:
    SESSION_STRING = <the string>
"""

import os
from pyrogram import Client

API_ID   = int(input("Enter API_ID: ").strip())
API_HASH = input("Enter API_HASH: ").strip()

with Client(
    "gen_session",
    api_id=API_ID,
    api_hash=API_HASH,
    in_memory=True,
) as app:
    session_string = app.export_session_string()

print("\n" + "="*60)
print("Your SESSION_STRING (add to Railway env vars):")
print("="*60)
print(session_string)
print("="*60 + "\n")
