"""
Team Builder mode.

Lets a user pick one option per category (e.g. build an outfit), then
save their whole selection as a set (not a single vote). Reuses the
tier_assignments table with tier="selected" instead of S/A/B/C/D/F,
since it already supports multiple rows per user per post.

Categories and their choices are collected one at a time in a chat
conversation after /teambuilder is run (see
utils/image_collect.collect_categories). Categories are capped at 4,
since a View allows at most 5 rows and one is needed for the Submit
button.
"""

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks
from utils.image_collect import collect_categories

MAX_PREVIEW_EMBEDS = 9
MAX_CATEGORIES = 4


def build_team_embed(question: str, categories: list[dict], summary: dict, submitters: int) -> discord.Embed:
    embed = discord.Embed(
        title=question,
        description="Pick one option per category, then hit Submit.",
        color=discord.Color.dark_teal(),
    )
    for cat in categories:
        lines = []
        for choice in cat["choices"]:
            label = choice["label"]
            item_key = f"{cat['category']}:{label}"
            count = summary.get(item_key, {}).get("selected", 0)
            pct = round(100 * count / submitters) if submitters else 0
            pic = " 🖼️" if choice.get("image_urls") else ""
            lines.append(f"{label}{pic} — {count} ({pct}%)")
        embed.add_field(name=cat["category"], value="\n".join(lines), inline=True)
    embed.set_footer(text=f"{submitters} submission(s)")
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, category: str, choices: list[dict], pending: dict):
        self.category = category
        self.choices_by_label = {c["label"]: c for c in choices}
        self.pending = pending
        options = [discord.SelectOption(label=c["label"]) for c in choices[:25]]
        super().__init__(placeholder=f"Choose {category}...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        choice_label = self.values[0]
        self.pending.setdefault(interaction.user.id, {})[self.category] = choice_label

        image_urls = self.choices_by_label.get(choice_label, {}).get("image_urls", [])
        image_embeds = []
        for url in image_urls[:MAX_PREVIEW_EMBEDS]:
            e = discord.Embed(title=f"{self.category}: {choice_label}") if not image_embeds else discord.Embed()
            e.set_image(url=url)
            image_embeds.append(e)

        await interaction.response.send_message(
            f"Set **{self.category}** to **{choice_label}**. Pick the rest, then hit Submit.",
            embeds=image_embeds,
            ephemeral=True,
        )


class SubmitButton(discord.ui.Button):
    def __init__(self, post_id: int, question: str, categories: list[dict], pending: dict, refresh_main_message):
        super().__init__(label="Submit Team", style=discord.ButtonStyle.success)
        self.post_id = post_id
        self.question = question
        self.categories = categories
        self.pending = pending
        self.refresh_main_message = refresh_main_message

    async def callback(self, interaction: discord.Interaction):
        user_picks = self.pending.get(interaction.user.id, {})
        missing = [c["category"] for c in self.categories if c["category"] not in user_picks]
        if missing:
            await interaction.response.send_message(
                f"Still need to pick: {', '.join(missing)}", ephemeral=True
            )
            return

        await db.clear_tier_selection(self.post_id, interaction.user.id)
        for category, choice in user_picks.items():
            await db.set_tier(self.post_id, interaction.user.id, f"{category}:{choice}", "selected")

        summary_text = ", ".join(f"{c}: {v}" for c, v in user_picks.items())
        await interaction.response.send_message(f"✅ Team submitted — {summary_text}", ephemeral=True)
        await self.refresh_main_message()


class TeamBuilderView(discord.ui.View):
    def __init__(self, post_id: int, question: str, categories: list[dict]):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.question = question
        self.categories = categories
        self.pending = {}  # user_id -> {category: choice}, in-memory scratch space

        for cat in categories[:MAX_CATEGORIES]:  # a View allows at most 5 rows; leave room for the submit button
            self.add_item(CategorySelect(cat["category"], cat["choices"], self.pending))
        self.add_item(SubmitButton(post_id, question, categories, self.pending, self.refresh_main_message))

    async def refresh_main_message(self):
        post = await db.get_post(self.post_id)
        if not post:
            return
        summary = await db.get_tier_summary(self.post_id)
        submitters = await db.count_distinct_tier_users(self.post_id)
        embed = build_team_embed(self.question, self.categories, summary, submitters)
        channel = self.message.guild.get_channel(post["channel_id"]) if self.message else None
        if channel:
            try:
                msg = await channel.fetch_message(post["message_id"])
                await msg.edit(embed=embed)
            except discord.NotFound:
                pass


class Builder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="teambuilder", description="Let people pick one option per category - you'll be asked for each category and choice")
    @app_commands.describe(title="Title for the build")
    async def teambuilder(self, interaction: discord.Interaction, title: str):
        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        await interaction.response.send_message("Creating team builder...")
        message = await interaction.original_response()

        category_list = await collect_categories(interaction, max_categories=MAX_CATEGORIES)
        if len(category_list) < 1:
            await message.edit(
                content="Team builder creation cancelled — didn't get at least 1 category "
                        "(with 2+ choices) in time."
            )
            return

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="team_builder",
            question=title,
            options=category_list,
        )

        view = TeamBuilderView(post_id, title, category_list)
        view.message = message
        embed = build_team_embed(title, category_list, {}, 0)
        await message.edit(content=None, embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Builder(bot))
