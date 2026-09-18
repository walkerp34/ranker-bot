"""
Moderator controls.

Two settings per server, stored in guild_settings:
  - posting_paused: when on, every creation command (poll, tier list,
    this-or-that, rating, team builder) refuses with a message.
    Existing posts keep working; this only blocks new ones.
  - required_role_id: when set, only members with that role can create
    posts. Leave unset to let anyone post.

Both commands require the "Manage Server" permission, same as most
other server-configuration actions in Discord.
"""

import discord
from discord import app_commands
from discord.ext import commands

from database import db


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="pauseposting", description="Pause or resume new posts being created in this server")
    @app_commands.describe(state="Turn posting on or off")
    @app_commands.choices(state=[
        app_commands.Choice(name="Pause (block new posts)", value="pause"),
        app_commands.Choice(name="Resume (allow new posts)", value="resume"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pauseposting(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        paused = state.value == "pause"
        await db.set_posting_paused(interaction.guild_id, paused)
        msg = "🚫 New posts are now **paused**." if paused else "✅ New posts are now **allowed** again."
        await interaction.response.send_message(msg)

    @app_commands.command(name="requirerole", description="Require a role to create posts, or clear the requirement")
    @app_commands.describe(role="The role required to post. Leave empty to remove the requirement.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def requirerole(self, interaction: discord.Interaction, role: discord.Role = None):
        await db.set_required_role(interaction.guild_id, role.id if role else None)
        if role:
            await interaction.response.send_message(f"✅ Only members with {role.mention} can now create posts.")
        else:
            await interaction.response.send_message("✅ Role requirement cleared — anyone can create posts.")

    @pauseposting.error
    @requirerole.error
    async def on_mod_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission to use this.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
