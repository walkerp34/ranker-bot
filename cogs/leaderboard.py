"""
Leaderboard mode - shows the top rated items (from /rate) and the
most-voted-for options (from /poll and /thisorthat) in this server.
"""

import discord
from discord import app_commands
from discord.ext import commands

from database import db

MEDALS = ["🥇", "🥈", "🥉"]


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Show the top rated items and most-voted options in this server")
    async def leaderboard(self, interaction: discord.Interaction):
        rating_rows = await db.get_rating_leaderboard(interaction.guild_id, limit=10)
        vote_rows = await db.get_vote_leaderboard(interaction.guild_id, limit=10)

        embed = discord.Embed(title="🏆 Leaderboard", color=discord.Color.gold())

        if rating_rows:
            lines = []
            for i, (question, avg_score, votes) in enumerate(rating_rows):
                prefix = MEDALS[i] if i < 3 else f"{i + 1}."
                lines.append(f"{prefix} **{question}** — {avg_score:.2f}/5 ({votes} rating(s))")
            embed.add_field(name="Top Rated (/rate)", value="\n".join(lines), inline=False)

        if vote_rows:
            lines = []
            for i, (choice, total_votes) in enumerate(vote_rows):
                prefix = MEDALS[i] if i < 3 else f"{i + 1}."
                lines.append(f"{prefix} **{choice}** — {total_votes} vote(s)")
            embed.add_field(name="Most Voted (/poll, /thisorthat)", value="\n".join(lines), inline=False)

        if not rating_rows and not vote_rows:
            embed.description = "Nothing to rank yet — try `/rate`, `/poll`, or `/thisorthat` to start."

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
