"""
Shared "can this person create a post right now?" check, used by every
creation command (poll, this-or-that, tier list, rating, team
builder). Centralized here so moderator settings apply consistently
everywhere instead of being re-implemented per mode.
"""

import discord

from database import db


async def check_can_post(interaction: discord.Interaction) -> tuple[bool, str]:
    """Returns (allowed, reason_if_blocked)."""
    settings = await db.get_guild_settings(interaction.guild_id)

    if settings["posting_paused"]:
        return False, "🚫 Posting is currently paused by a moderator. Try again later."

    role_id = settings["required_role_id"]
    if role_id:
        member = interaction.user
        member_roles = getattr(member, "roles", [])
        if not any(role.id == role_id for role in member_roles):
            return False, f"🚫 You need the <@&{role_id}> role to create posts here."

    return True, ""
