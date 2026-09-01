"""
Generate a WZGram / Pyrogram SESSION_STRING.

Usage:
    python gen_session.py

Then copy the printed string and add it to your environment variables / .env as:
    SESSION_STRING = <the string>
"""

import asyncio
import os
import sys

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from wzgram import Client


async def generate_session():
    print("=" * 60)
    print(" Telegram WZGram / Pyrogram Session String Generator")
    print("=" * 60)
    print("Get your API_ID and API_HASH from https://my.telegram.org\n")

    env_api_id = os.environ.get("API_ID", "").strip()
    env_api_hash = os.environ.get("API_HASH", "").strip()

    if env_api_id:
        api_id_input = input(f"Enter API_ID [{env_api_id}]: ").strip()
        api_id_str = api_id_input if api_id_input else env_api_id
    else:
        api_id_str = input("Enter API_ID: ").strip()

    try:
        api_id = int(api_id_str)
    except ValueError:
        print("\nError: API_ID must be a valid integer.")
        sys.exit(1)

    if env_api_hash:
        api_hash_input = input(f"Enter API_HASH [{env_api_hash}]: ").strip()
        api_hash = api_hash_input if api_hash_input else env_api_hash
    else:
        api_hash = input("Enter API_HASH: ").strip()

    if not api_hash:
        print("\nError: API_HASH cannot be empty.")
        sys.exit(1)

    print("\nConnecting to Telegram to authorize session...")
    print("(You will be prompted for your phone number and Telegram login code)\n")

    async with Client(
        name="gen_session",
        api_id=api_id,
        api_hash=api_hash,
        in_memory=True,
    ) as app:
        session_string = await app.export_session_string()

    print("\n" + "=" * 60)
    print("SUCCESS! Your SESSION_STRING:")
    print("=" * 60)
    print(session_string)
    print("=" * 60)
    print("\nAdd this string to your environment variables / .env as:")
    print("SESSION_STRING=" + session_string + "\n")


def main():
    try:
        asyncio.run(generate_session())
    except KeyboardInterrupt:
        print("\n\nSession generation cancelled by user.")
    except Exception as e:
        print(f"\nFailed to generate session string: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
