"""
Ranker Bot - main entry point.

Loads config, connects to Discord, loads all cogs (feature modules)
from the cogs/ folder, and starts the bot.
"""

import asyncio
import logging

import discord
from discord.ext import commands

from config import DISCORD_TOKEN
from database.db import init_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ranker-bot")

# Which permissions the bot needs from Discord to function.
intents = discord.Intents.default()
# Needed so the bot can see attachments in follow-up messages when
# collecting images for poll/tier-list/rating/budget/team builder
# (see utils/image_collect.py). Must also be turned on in the
# Discord Developer Portal: Bot tab -> Privileged Gateway Intents ->
# Message Content Intent.
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Cogs are self-contained feature modules. Add a new file to cogs/
# and list it here to plug in a new "mode" (poll, tier list, etc).
STARTUP_COGS = [
    "cogs.polls",
    "cogs.tier_list",
    "cogs.this_or_that",
    "cogs.rating",
    "cogs.leaderboard",
    "cogs.moderation",
    "cogs.builder",
    "cogs.home",
]


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (id: {bot.user.id})")
    synced = await bot.tree.sync()
    log.info(f"Synced {len(synced)} slash command(s)")


async def main():
    await init_db()

    async with bot:
        for cog in STARTUP_COGS:
            await bot.load_extension(cog)
            log.info(f"Loaded cog: {cog}")
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
