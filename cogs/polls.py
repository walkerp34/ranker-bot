"""
Poll mode ("This or That" / Multiple Choice).

Supports multiple images per poll (not per option) - Discord lets one
message carry several embeds, so each extra photo just gets its own
image-only embed alongside the main results embed, rather than
compositing them into one picture.
"""

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks
from utils.image_collect import prompt_multi_image

MAX_EXTRA_IMAGE_EMBEDS = 9  # + 1 main embed = 10, Discord's per-message cap


def build_results_embeds(question: str, options: list[str], counts: dict[str, int],
                          image_urls: list[str] = None) -> list[discord.Embed]:
    total = sum(counts.values()) or 1
    main = discord.Embed(title=question, color=discord.Color.blurple())
    for option in options:
        count = counts.get(option, 0)
        pct = round(100 * count / total)
        bar_filled = "█" * (pct // 10)
        bar_empty = "░" * (10 - pct // 10)
        main.add_field(
            name=option,
            value=f"{bar_filled}{bar_empty}  {count} vote(s) ({pct}%)",
            inline=False,
        )
    main.set_footer(text=f"{sum(counts.values())} total vote(s)")

    embeds = [main]
    image_urls = image_urls or []
    if image_urls:
        main.set_image(url=image_urls[0])
        for url in image_urls[1:1 + MAX_EXTRA_IMAGE_EMBEDS]:
            extra = discord.Embed()
            extra.set_image(url=url)
            embeds.append(extra)
    return embeds


class PollView(discord.ui.View):
    """Renders one button per option and handles vote clicks."""

    def __init__(self, post_id: int, question: str, options: list[str], image_urls: list[str] = None):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.question = question
        self.options = options
        self.image_urls = image_urls or []
        for option in options:
            self.add_item(self._make_button(option))

    def _make_button(self, option: str) -> discord.ui.Button:
        button = discord.ui.Button(label=option, style=discord.ButtonStyle.primary)

        async def callback(interaction: discord.Interaction):
            await db.cast_vote(self.post_id, interaction.user.id, option)
            counts = await db.get_vote_counts(self.post_id)
            embeds = build_results_embeds(self.question, self.options, counts, self.image_urls)
            await interaction.response.edit_message(embeds=embeds, view=self)

        button.callback = callback
        return button


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="poll", description="Create a poll with up to 4 options")
    @app_commands.describe(
        question="The question to ask",
        option1="First option",
        option2="Second option",
        option3="Optional third option",
        option4="Optional fourth option",
        image="Optional image to show with the poll (you can add more after creating it)",
    )
    async def poll(
        self,
        interaction: discord.Interaction,
        question: str,
        option1: str,
        option2: str,
        option3: str = None,
        option4: str = None,
        image: discord.Attachment = None,
    ):
        options = [o for o in [option1, option2, option3, option4] if o]

        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        image_urls = [image.url] if image else []

        await interaction.response.send_message("Creating poll...")
        message = await interaction.original_response()

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="poll",
            question=question,
            options=options,
        )

        view = PollView(post_id, question, options, image_urls)
        embeds = build_results_embeds(question, options, {}, image_urls)
        await message.edit(content=None, embeds=embeds, view=view)

        more = await prompt_multi_image(
            interaction,
            "Want to add more image(s) to this poll? Upload them one at a time, "
            "type `done` when finished, or ignore to skip (60s per upload).",
        )
        if more:
            image_urls = image_urls + [url for url, _ in more]
            view.image_urls = image_urls
            embeds = build_results_embeds(question, options, {}, image_urls)
            await message.edit(embeds=embeds)


async def setup(bot: commands.Bot):
    await bot.add_cog(Polls(bot))
