"""
Tier List mode.

Each item can carry MULTIPLE images. Up to 6 can be attached directly
via /tierlist's image1-image6 params (one per item, matched by order);
after that, a follow-up chat prompt lets the creator add more images
per item (or give images to items that had none).

The main results message shows a captioned overview grid (one
thumbnail per item, via Pillow). When someone picks an item to tier
it, they see ALL of that item's images as separate embeds (Discord
allows several embeds per message, so no compositing needed there).
"""

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks
from utils.image_collect import prompt_per_item_images

TIERS = ["S", "A", "B", "C", "D", "F"]
MAX_IMAGE_SLOTS = 6
MAX_PREVIEW_EMBEDS = 9

THUMB_SIZE = 160
LABEL_HEIGHT = 28
GRID_GAP = 10
GRID_COLUMNS = 3


def compose_grid(labeled_images: list[tuple[str, bytes]]):
    """Builds a captioned overview grid of one thumbnail per item, wrapping
    after GRID_COLUMNS per row. Used for the main results message only."""
    import io
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=16)
    except TypeError:
        font = ImageFont.load_default()

    columns = min(GRID_COLUMNS, len(labeled_images))
    rows = (len(labeled_images) + columns - 1) // columns

    cell_w = THUMB_SIZE + GRID_GAP
    cell_h = THUMB_SIZE + LABEL_HEIGHT + GRID_GAP
    canvas = Image.new("RGB", (columns * cell_w, rows * cell_h), "#2b2d31")
    draw = ImageDraw.Draw(canvas)

    for i, (label, img_bytes) in enumerate(labeled_images):
        col, row = i % columns, i // columns
        x, y = col * cell_w, row * cell_h

        thumb = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
        paste_x = x + (THUMB_SIZE - thumb.width) // 2
        paste_y = y + (THUMB_SIZE - thumb.height) // 2
        canvas.paste(thumb, (paste_x, paste_y))

        text = label if len(label) <= 20 else label[:17] + "..."
        text_w = draw.textlength(text, font=font)
        draw.text((x + (THUMB_SIZE - text_w) / 2, y + THUMB_SIZE + 4), text, fill="white", font=font)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def community_tier(tier_counts: dict[str, int]) -> str:
    """The most-picked tier for an item; ties broken by S>A>B>C>D>F order."""
    if not tier_counts:
        return "—"
    best = max(TIERS, key=lambda t: tier_counts.get(t, 0))
    return best if tier_counts.get(best, 0) > 0 else "—"


def build_results_embed(question: str, items: list[dict], summary: dict, has_grid: bool) -> discord.Embed:
    embed = discord.Embed(
        title=question,
        description="Pick an item below to give it a tier. Results update as people vote.",
        color=discord.Color.purple(),
    )
    by_tier = {t: [] for t in TIERS}
    unranked = []
    for item in items:
        label = item["label"]
        counts = summary.get(label, {})
        tier = community_tier(counts)
        if tier == "—":
            unranked.append(label)
        else:
            total_votes = sum(counts.values())
            by_tier[tier].append(f"{label} ({total_votes} vote(s))")

    for tier in TIERS:
        if by_tier[tier]:
            embed.add_field(name=f"Tier {tier}", value="\n".join(by_tier[tier]), inline=False)
    if unranked:
        embed.add_field(name="Not yet tiered", value="\n".join(unranked), inline=False)

    if has_grid:
        embed.set_image(url="attachment://tierlist_grid.png")
    return embed


class TierButtonsView(discord.ui.View):
    """Shown alongside an item's image preview after it's picked from the dropdown."""

    def __init__(self, post_id: int, label: str, on_voted):
        super().__init__(timeout=120)
        self.post_id = post_id
        self.label = label
        self.on_voted = on_voted
        for tier in TIERS:
            self.add_item(self._make_button(tier))

    def _make_button(self, tier: str) -> discord.ui.Button:
        button = discord.ui.Button(label=tier, style=discord.ButtonStyle.secondary)

        async def callback(interaction: discord.Interaction):
            await db.set_tier(self.post_id, interaction.user.id, self.label, tier)
            await interaction.response.edit_message(
                content=f"You gave **{self.label}** tier **{tier}**. ✅", embeds=[], view=None
            )
            await self.on_voted()

        button.callback = callback
        return button


class ItemSelect(discord.ui.Select):
    def __init__(self, post_id: int, items: list[dict], refresh_main_message):
        self.post_id = post_id
        self.items = items
        self.refresh_main_message = refresh_main_message
        options = [discord.SelectOption(label=item["label"][:100]) for item in items[:25]]
        super().__init__(placeholder="Choose an item to tier...", options=options)

    async def callback(self, interaction: discord.Interaction):
        label = self.values[0]
        item = next((i for i in self.items if i["label"] == label), None)
        image_urls = (item.get("image_urls") if item else None) or []

        async def on_voted():
            await self.refresh_main_message()

        embeds = []
        for url in image_urls[:MAX_PREVIEW_EMBEDS]:
            e = discord.Embed(title=f"Tier this: {label}") if not embeds else discord.Embed()
            e.set_image(url=url)
            embeds.append(e)

        await interaction.response.send_message(
            content=f"Give **{label}** a tier:" if not embeds else None,
            embeds=embeds,
            view=TierButtonsView(self.post_id, label, on_voted),
            ephemeral=True,
        )


class TierListView(discord.ui.View):
    def __init__(self, post_id: int, question: str, items: list[dict], has_grid: bool):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.question = question
        self.items = items
        self.has_grid = has_grid
        self.add_item(ItemSelect(post_id, items, self.refresh_main_message))

    async def refresh_main_message(self):
        post = await db.get_post(self.post_id)
        if not post:
            return
        summary = await db.get_tier_summary(self.post_id)
        embed = build_results_embed(self.question, self.items, summary, self.has_grid)
        channel = self.message.guild.get_channel(post["channel_id"]) if self.message else None
        if channel:
            try:
                msg = await channel.fetch_message(post["message_id"])
                await msg.edit(embed=embed)
            except discord.NotFound:
                pass


class TierList(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="tierlist", description="Create a tier list for people to sort items into S/A/B/C/D/F")
    @app_commands.describe(
        title="Title for the tier list",
        items="Comma-separated list of items to rank (e.g. 'Banana, Apple, Cheese')",
        image1="Image for the 1st item (you can add more per item after creating it)",
        image2="Image for the 2nd item",
        image3="Image for the 3rd item",
        image4="Image for the 4th item",
        image5="Image for the 5th item",
        image6="Image for the 6th item",
    )
    async def tierlist(
        self,
        interaction: discord.Interaction,
        title: str,
        items: str,
        image1: discord.Attachment = None,
        image2: discord.Attachment = None,
        image3: discord.Attachment = None,
        image4: discord.Attachment = None,
        image5: discord.Attachment = None,
        image6: discord.Attachment = None,
    ):
        labels = [i.strip() for i in items.split(",") if i.strip()]
        if len(labels) < 2:
            await interaction.response.send_message(
                "Give at least two comma-separated items.", ephemeral=True
            )
            return
        if len(labels) > 25:
            await interaction.response.send_message(
                "Max 25 items (Discord's dropdown limit).", ephemeral=True
            )
            return

        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        await interaction.response.defer()

        attachments = [image1, image2, image3, image4, image5, image6]
        item_list = [
            {"label": label, "image_urls": [attachments[i].url] if i < len(attachments) and attachments[i] else []}
            for i, label in enumerate(labels)
        ]
        thumb_bytes = {}
        for i, label in enumerate(labels):
            if i < len(attachments) and attachments[i]:
                thumb_bytes[label] = await attachments[i].read()

        has_grid = len(thumb_bytes) > 0
        embed = build_results_embed(title, item_list, {}, has_grid)

        if has_grid:
            grid = compose_grid(list(thumb_bytes.items()))
            file = discord.File(grid, filename="tierlist_grid.png")
            message = await interaction.followup.send(embed=embed, file=file, wait=True)
        else:
            message = await interaction.followup.send(embed=embed, wait=True)

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="tier_list",
            question=title,
            options=item_list,
        )

        view = TierListView(post_id, title, item_list, has_grid)
        view.message = message
        await message.edit(view=view)

        results = await prompt_per_item_images(interaction, labels)
        if results:
            for item in item_list:
                label = item["label"]
                if label in results:
                    item["image_urls"] += [url for url, _ in results[label]]
                    if label not in thumb_bytes:
                        thumb_bytes[label] = results[label][0][1]
            await db.update_post_options(post_id, item_list)

            has_grid = len(thumb_bytes) > 0
            new_view = TierListView(post_id, title, item_list, has_grid)
            new_view.message = message
            embed = build_results_embed(title, item_list, {}, has_grid)
            if has_grid:
                grid = compose_grid(list(thumb_bytes.items()))
                file = discord.File(grid, filename="tierlist_grid.png")
                await message.edit(embed=embed, attachments=[file], view=new_view)
            else:
                await message.edit(embed=embed, view=new_view)


async def setup(bot: commands.Bot):
    await bot.add_cog(TierList(bot))
