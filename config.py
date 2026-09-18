"""
Loads settings from a .env file so secrets never get hard-coded
or committed to source control.
"""

import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Copy .env.example to .env and fill in "
        "your bot token from the Discord Developer Portal."
    )
