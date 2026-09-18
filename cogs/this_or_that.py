"""
This or That mode - head-to-head picks between two images.

Discord embeds can only show one image each, so the two submitted
images are combined side by side into a single composite image
(using Pillow) before posting. Voting works the same way as polls:
a button per side, live results after each click.
"""

import io

import discord
from PIL import Image
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks

TARGET_HEIGHT = 400


def compose_side_by_side(img_a: bytes, img_b: bytes) -> io.BytesIO:
    """Combine two images into one, matched to the same height, side by side."""
    a = Image.open(io.BytesIO(img_a)).convert("RGB")
    b = Image.open(io.BytesIO(img_b)).convert("RGB")

    a = a.resize((int(a.width * TARGET_HEIGHT / a.height), TARGET_HEIGHT))
    b = b.resize((int(b.width * TARGET_HEIGHT / b.height), TARGET_HEIGHT))

    gap = 12
    canvas = Image.new("RGB", (a.width + gap + b.width, TARGET_HEIGHT), "black")
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + gap, 0))

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def build_results_embed(question: str, label_a: str, label_b: str, counts: dict[str, int]) -> discord.Embed:
    count_a = counts.get(label_a, 0)
    count_b = counts.get(label_b, 0)
    total = count_a + count_b or 1
    embed = discord.Embed(title=question, color=discord.Color.gold())
    embed.add_field(name=f"⬅️ {label_a}", value=f"{count_a} vote(s) ({round(100 * count_a / total)}%)")
    embed.add_field(name=f"➡️ {label_b}", value=f"{count_b} vote(s) ({round(100 * count_b / total)}%)")
    embed.set_footer(text=f"{count_a + count_b} total vote(s)")
    embed.set_image(url="attachment://thisorthat.png")
    return embed


class ThisOrThatView(discord.ui.View):
    def __init__(self, post_id: int, question: str, label_a: str, label_b: str):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.question = question
        self.label_a = label_a
        self.label_b = label_b

        self.add_item(self._make_button(label_a, discord.ButtonStyle.primary))
        self.add_item(self._make_button(label_b, discord.ButtonStyle.success))

    def _make_button(self, label: str, style: discord.ButtonStyle) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=style)

        async def callback(interaction: discord.Interaction):
            await db.cast_vote(self.post_id, interaction.user.id, label)
            counts = await db.get_vote_counts(self.post_id)
            embed = build_results_embed(self.question, self.label_a, self.label_b, counts)
            # Editing here keeps the already-attached composite image; we don't
            # need to re-send the file.
            await interaction.response.edit_message(embed=embed, view=self)

        button.callback = callback
        return button


class ThisOrThat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="thisorthat", description="Head-to-head vote between two images")
    @app_commands.describe(
        question="What are people choosing between?",
        image_a="First image",
        image_b="Second image",
        label_a="Optional label for the first image (defaults to 'A')",
        label_b="Optional label for the second image (defaults to 'B')",
    )
    async def this_or_that(
        self,
        interaction: discord.Interaction,
        question: str,
        image_a: discord.Attachment,
        image_b: discord.Attachment,
        label_a: str = "A",
        label_b: str = "B",
    ):
        await interaction.response.defer()

        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.followup.send(reason, ephemeral=True)
            return

        composite = compose_side_by_side(await image_a.read(), await image_b.read())
        file = discord.File(composite, filename="thisorthat.png")

        embed = build_results_embed(question, label_a, label_b, {})
        message = await interaction.followup.send(embed=embed, file=file, wait=True)

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="this_or_that",
            question=question,
            options=[label_a, label_b],
        )

        view = ThisOrThatView(post_id, question, label_a, label_b)
        await message.edit(view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(ThisOrThat(bot))
