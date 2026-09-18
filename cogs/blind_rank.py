"""
Blind Ranking mode.

Creator runs /blindrank with a title, then adds items via the chat
wizard (name, then image(s), repeat, "done" to finish). The bot posts
a public message with a "Start Ranking" button.

Each participant who clicks it gets a PRIVATE (ephemeral) session:
shown one item at a time with its image(s), and picks a position
(1 through N) for it from a dropdown, with already-used positions
removed. Once they've placed every item, their full ranking saves.
Nobody sees anyone else's in-progress or individual picks — only the
aggregated average position per item, shown on the public post.

Reuses the tier_assignments table (post_id, user_id, item, tier) with
the position number stored as the "tier" string, same trick Team
Builder uses with tier="selected".
"""

import discord
from discord import app_commands
from discord.ext import commands
import json

from database import db
from utils import checks
from utils.image_collect import prompt_named_items
from cogs.tier_list import compose_grid

MAX_PREVIEW_EMBEDS = 9
MIN_ITEMS = 3


def build_results_embed(title: str, items: list[dict], averages: dict, has_grid: bool) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description="Click **Start Ranking** below to rank every item privately, one at a time.",
        color=discord.Color.dark_gold(),
    )
    if averages:
        ranked = sorted(averages.items(), key=lambda kv: kv[1][0])  # by avg position, best first
        lines = [f"{i + 1}. **{label}** — avg position {avg:.1f} ({count} ranking(s))"
                 for i, (label, (avg, count)) in enumerate(ranked)]
        embed.add_field(name="Current standings", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Current standings", value="Nobody has ranked yet.", inline=False)
    embed.set_footer(text=f"{len(items)} item(s) to rank")
    if has_grid:
        embed.set_image(url="attachment://blindrank_grid.png")
    return embed


class RankSession:
    """One user's in-progress ranking. Lives only in memory for the bot's
    current run — a restart mid-session loses progress, same limitation as
    the bot's other in-memory view state."""

    def __init__(self, items: list[dict]):
        self.items = items
        self.index = 0
        self.used_positions = set()
        self.assignments = {}  # label -> position

    @property
    def total(self):
        return len(self.items)

    @property
    def current_item(self):
        return self.items[self.index] if self.index < self.total else None

    @property
    def remaining_positions(self):
        return [p for p in range(1, self.total + 1) if p not in self.used_positions]

    @property
    def done(self):
        return self.index >= self.total

    def assign(self, position: int):
        item = self.current_item
        self.assignments[item["label"]] = position
        self.used_positions.add(position)
        self.index += 1


def build_item_embeds(session: RankSession) -> list[discord.Embed]:
    item = session.current_item
    main = discord.Embed(
        title=item["label"],
        description=f"Item {session.index + 1} of {session.total} — pick this item's position.",
        color=discord.Color.dark_gold(),
    )
    embeds = [main]
    image_urls = item.get("image_urls") or []
    if image_urls:
        main.set_image(url=image_urls[0])
        for url in image_urls[1:1 + MAX_PREVIEW_EMBEDS]:
            extra = discord.Embed()
            extra.set_image(url=url)
            embeds.append(extra)
    return embeds


class PositionSelect(discord.ui.Select):
    def __init__(self, post_id: int, session: RankSession, refresh_main_message):
        self.post_id = post_id
        self.session = session
        self.refresh_main_message = refresh_main_message
        options = [discord.SelectOption(label=f"Position {p}", value=str(p))
                   for p in session.remaining_positions]
        super().__init__(placeholder="Pick a position for this item...", options=options)

    async def callback(self, interaction: discord.Interaction):
        position = int(self.values[0])
        self.session.assign(position)

        if self.session.done:
            for label, pos in self.session.assignments.items():
                await db.set_tier(self.post_id, interaction.user.id, label, str(pos))
            await interaction.response.edit_message(
                content=f"✅ Ranking submitted — you placed all {self.session.total} items!",
                embeds=[], view=None,
            )
            await self.refresh_main_message()
        else:
            embeds = build_item_embeds(self.session)
            view = discord.ui.View(timeout=300)
            view.add_item(PositionSelect(self.post_id, self.session, self.refresh_main_message))
            await interaction.response.edit_message(embeds=embeds, view=view)


class BlindRankView(discord.ui.View):
    def __init__(self, post_id: int, title: str, items: list[dict], has_grid: bool):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.title = title
        self.items = items
        self.has_grid = has_grid

    @discord.ui.button(label="🎯 Start Ranking", style=discord.ButtonStyle.primary, custom_id="blindrank_start")
    async def start_ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        post = await db.get_post(self.post_id)
        if not post:
            await interaction.response.send_message("This ranking no longer exists.", ephemeral=True)
            return
        items = post["options"] if isinstance(post["options"], list) else json.loads(post["options"])
        if len(items) < 2:
            await interaction.response.send_message("Not enough items to rank.", ephemeral=True)
            return

        session = RankSession(items)
        embeds = build_item_embeds(session)
        view = discord.ui.View(timeout=300)
        view.add_item(PositionSelect(self.post_id, session, self.refresh_main_message))
        await interaction.response.send_message(embeds=embeds, view=view, ephemeral=True)

    async def refresh_main_message(self):
        post = await db.get_post(self.post_id)
        if not post:
            return
        rows = await db.get_average_position(self.post_id)
        averages = {item: (avg, count) for item, avg, count in rows}
        embed = build_results_embed(self.title, self.items, averages, self.has_grid)
        channel = self.message.guild.get_channel(post["channel_id"]) if self.message else None
        if channel:
            try:
                msg = await channel.fetch_message(post["message_id"])
                await msg.edit(embed=embed)
            except discord.NotFound:
                pass


class BlindRank(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="blindrank", description="Create a blind ranking: rank items 1-N, privately, one at a time")
    @app_commands.describe(title="Title for the ranking")
    async def blindrank(self, interaction: discord.Interaction, title: str):
        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        await interaction.response.defer()

        items, thumb_bytes = await prompt_named_items(interaction, min_items=MIN_ITEMS)
        if len(items) < MIN_ITEMS:
            await interaction.followup.send(
                f"Didn't get enough items (need at least {MIN_ITEMS}) — nothing was posted.",
                ephemeral=True,
            )
            return

        has_grid = len(thumb_bytes) > 0
        embed = build_results_embed(title, items, {}, has_grid)

        if has_grid:
            grid = compose_grid(list(thumb_bytes.items()))
            file = discord.File(grid, filename="blindrank_grid.png")
            message = await interaction.followup.send(embed=embed, file=file, wait=True)
        else:
            message = await interaction.followup.send(embed=embed, wait=True)

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="blind_rank",
            question=title,
            options=items,
        )

        view = BlindRankView(post_id, title, items, has_grid)
        view.message = message
        await message.edit(view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(BlindRank(bot))
