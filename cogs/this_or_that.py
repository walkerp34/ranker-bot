"""
This or That mode - head-to-head picks between two options.

Each side is added via the standardized name-then-image(s) chat flow
(see utils/image_collect.collect_named_options), so a side can carry
multiple images - but Discord embeds can only show one image each, so
when both sides have at least one image, their first images are
combined side by side into a single composite image (using Pillow)
before posting. Voting works the same way as polls: a button per side,
live results after each click.
"""

import io

import aiohttp
import discord
from PIL import Image
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks
from utils.image_collect import collect_named_options

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


async def _fetch_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read()


def build_results_embed(question: str, label_a: str, label_b: str, counts: dict[str, int],
                         has_composite: bool) -> discord.Embed:
    count_a = counts.get(label_a, 0)
    count_b = counts.get(label_b, 0)
    total = count_a + count_b or 1
    embed = discord.Embed(title=question, color=discord.Color.gold())
    embed.add_field(name=f"⬅️ {label_a}", value=f"{count_a} vote(s) ({round(100 * count_a / total)}%)")
    embed.add_field(name=f"➡️ {label_b}", value=f"{count_b} vote(s) ({round(100 * count_b / total)}%)")
    embed.set_footer(text=f"{count_a + count_b} total vote(s)")
    if has_composite:
        embed.set_image(url="attachment://thisorthat.png")
    return embed


class ThisOrThatView(discord.ui.View):
    def __init__(self, post_id: int, question: str, label_a: str, label_b: str, has_composite: bool):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.question = question
        self.label_a = label_a
        self.label_b = label_b
        self.has_composite = has_composite

        self.add_item(self._make_button(label_a, discord.ButtonStyle.primary))
        self.add_item(self._make_button(label_b, discord.ButtonStyle.success))

    def _make_button(self, label: str, style: discord.ButtonStyle) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=style)

        async def callback(interaction: discord.Interaction):
            await db.cast_vote(self.post_id, interaction.user.id, label)
            counts = await db.get_vote_counts(self.post_id)
            embed = build_results_embed(self.question, self.label_a, self.label_b, counts, self.has_composite)
            # Editing here keeps the already-attached composite image (if any);
            # we don't need to re-send the file.
            await interaction.response.edit_message(embed=embed, view=self)

        button.callback = callback
        return button


class ThisOrThat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="thisorthat", description="Head-to-head vote between two options - you'll be asked for each one")
    @app_commands.describe(question="What are people choosing between?")
    async def this_or_that(self, interaction: discord.Interaction, question: str):
        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        await interaction.response.send_message("Creating this-or-that...")
        message = await interaction.original_response()

        sides = await collect_named_options(interaction, "side", exact_items=2)
        if len(sides) < 2:
            await message.edit(content="This-or-that creation cancelled — didn't get both sides in time.")
            return

        side_a, side_b = sides
        label_a, label_b = side_a["label"], side_b["label"]

        file = None
        has_composite = bool(side_a["image_urls"] and side_b["image_urls"])
        if has_composite:
            img_a = await _fetch_bytes(side_a["image_urls"][0])
            img_b = await _fetch_bytes(side_b["image_urls"][0])
            composite = compose_side_by_side(img_a, img_b)
            file = discord.File(composite, filename="thisorthat.png")

        embed = build_results_embed(question, label_a, label_b, {}, has_composite)
        if file:
            await message.edit(content=None, embed=embed, attachments=[file])
        else:
            await message.edit(content=None, embed=embed)

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="this_or_that",
            question=question,
            options=sides,
        )

        view = ThisOrThatView(post_id, question, label_a, label_b, has_composite)
        await message.edit(view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(ThisOrThat(bot))
