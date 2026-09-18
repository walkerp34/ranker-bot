"""
Rating mode - rate a single item (optionally with images) 1-5, results
show the live average. Supports multiple images per item, shown as
extra embeds alongside the main one (Discord allows several embeds
per message).
"""

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks
from utils.image_collect import prompt_multi_image

STARS = "⭐"
MAX_EXTRA_IMAGE_EMBEDS = 9


def build_results_embeds(question: str, avg: float, count: int, image_urls: list[str] = None) -> list[discord.Embed]:
    main = discord.Embed(
        title=question,
        description=f"{STARS * round(avg)}  **{avg:.2f}** / 5  ({count} rating(s))" if count else "No ratings yet.",
        color=discord.Color.magenta(),
    )
    embeds = [main]
    image_urls = image_urls or []
    if image_urls:
        main.set_image(url=image_urls[0])
        for url in image_urls[1:1 + MAX_EXTRA_IMAGE_EMBEDS]:
            extra = discord.Embed()
            extra.set_image(url=url)
            embeds.append(extra)
    return embeds


class RatingView(discord.ui.View):
    def __init__(self, post_id: int, question: str, image_urls: list[str] = None):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.question = question
        self.image_urls = image_urls or []
        for score in range(1, 6):
            self.add_item(self._make_button(score))

    def _make_button(self, score: int) -> discord.ui.Button:
        button = discord.ui.Button(label=f"{score} {STARS}", style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction):
            await db.cast_vote(self.post_id, interaction.user.id, str(score))
            avg, count = await db.get_average_rating(self.post_id)
            embeds = build_results_embeds(self.question, avg, count, self.image_urls)
            await interaction.response.edit_message(embeds=embeds, view=self)

        button.callback = callback
        return button


class Rating(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="rate", description="Post something for people to rate 1-5 stars")
    @app_commands.describe(
        title="What are people rating?",
        image="Optional image to show alongside it (you can add more after creating it)",
    )
    async def rate(self, interaction: discord.Interaction, title: str, image: discord.Attachment = None):
        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        image_urls = [image.url] if image else []

        await interaction.response.send_message("Creating rating post...")
        message = await interaction.original_response()

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="rating",
            question=title,
            options=["1", "2", "3", "4", "5"],
        )

        view = RatingView(post_id, title, image_urls)
        embeds = build_results_embeds(title, 0.0, 0, image_urls)
        await message.edit(content=None, embeds=embeds, view=view)

        more = await prompt_multi_image(
            interaction,
            "Want to add more image(s) to this rating post? Upload them one at a time, "
            "type `done` when finished, or ignore to skip (60s per upload).",
        )
        if more:
            image_urls = image_urls + [url for url, _ in more]
            view.image_urls = image_urls
            avg, count = await db.get_average_rating(post_id)
            embeds = build_results_embeds(title, avg, count, image_urls)
            await message.edit(embeds=embeds)


async def setup(bot: commands.Bot):
    await bot.add_cog(Rating(bot))
