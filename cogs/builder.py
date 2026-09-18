"""
Budget Builder and Team Builder modes.

Both let a user pick multiple items via a dropdown, then save their
selection as a set (not a single vote). They reuse the tier_assignments
table with tier="selected" instead of S/A/B/C/D/F, since it already
supports multiple rows per user per post.

Budget Builder: items have a price; a user's total selection must not
exceed the stated budget. Each item can carry multiple images,
collected via a follow-up chat prompt after creation.

Team Builder: items are grouped into categories (e.g. Color, Size);
a user must pick exactly one item per category. Each choice can
likewise carry multiple images, collected the same way.
"""

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks
from utils.image_collect import prompt_per_item_images

MAX_PREVIEW_EMBEDS = 9


# ---------------------------------------------------------------------
# Budget Builder
# ---------------------------------------------------------------------

def parse_priced_items(text: str) -> list[dict]:
    """Parses 'Hoodie:30, Hat:15, Sticker:5' into
    [{"label": "Hoodie", "price": 30.0, "image_urls": []}, ...]."""
    items = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        label, price_str = chunk.rsplit(":", 1)
        try:
            price = float(price_str.strip())
        except ValueError:
            continue
        items.append({"label": label.strip(), "price": price, "image_urls": []})
    return items


def build_budget_embed(question: str, budget: float, items: list[dict], summary: dict, submitters: int) -> discord.Embed:
    embed = discord.Embed(
        title=question,
        description=f"Budget: **${budget:.2f}**. Pick items below without going over.",
        color=discord.Color.teal(),
    )
    lines = []
    for item in items:
        count = summary.get(item["label"], {}).get("selected", 0)
        pct = round(100 * count / submitters) if submitters else 0
        pic = " 🖼️" if item.get("image_urls") else ""
        lines.append(f"**{item['label']}**{pic} (${item['price']:.2f}) — included in {count} build(s) ({pct}%)")
    embed.add_field(name="Items", value="\n".join(lines) if lines else "No items.", inline=False)
    embed.set_footer(text=f"{submitters} build(s) submitted")
    for item in items:
        if item.get("image_urls"):
            embed.set_thumbnail(url=item["image_urls"][0])
            break
    return embed


class BudgetSelect(discord.ui.Select):
    def __init__(self, post_id: int, question: str, budget: float, items: list[dict], refresh_main_message):
        self.post_id = post_id
        self.question = question
        self.budget = budget
        self.items = items
        self.refresh_main_message = refresh_main_message
        options = [
            discord.SelectOption(label=f"{i['label']} (${i['price']:.2f})", value=i["label"])
            for i in items[:25]
        ]
        super().__init__(
            placeholder="Pick items for your build...",
            options=options,
            min_values=1,
            max_values=len(options),
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = set(self.values)
        total = sum(i["price"] for i in self.items if i["label"] in chosen)

        if total > self.budget:
            await interaction.response.send_message(
                f"That build totals **${total:.2f}**, which is over your **${self.budget:.2f}** budget. "
                f"Deselect something and try again.",
                ephemeral=True,
            )
            return

        await db.clear_tier_selection(self.post_id, interaction.user.id)
        for label in chosen:
            await db.set_tier(self.post_id, interaction.user.id, label, "selected")

        image_embeds = []
        for item in self.items:
            if item["label"] in chosen:
                for url in item.get("image_urls", []):
                    if len(image_embeds) >= MAX_PREVIEW_EMBEDS:
                        break
                    e = discord.Embed(title=item["label"])
                    e.set_image(url=url)
                    image_embeds.append(e)

        await interaction.response.send_message(
            f"✅ Saved your build: {', '.join(chosen) or '(nothing)'} — **${total:.2f}** / ${self.budget:.2f}",
            embeds=image_embeds,
            ephemeral=True,
        )
        await self.refresh_main_message()


class BudgetBuilderView(discord.ui.View):
    def __init__(self, post_id: int, question: str, budget: float, items: list[dict]):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.question = question
        self.budget = budget
        self.items = items
        self.add_item(BudgetSelect(post_id, question, budget, items, self.refresh_main_message))

    async def refresh_main_message(self):
        post = await db.get_post(self.post_id)
        if not post:
            return
        summary = await db.get_tier_summary(self.post_id)
        submitters = await db.count_distinct_tier_users(self.post_id)
        embed = build_budget_embed(self.question, self.budget, self.items, summary, submitters)
        channel = self.message.guild.get_channel(post["channel_id"]) if self.message else None
        if channel:
            try:
                msg = await channel.fetch_message(post["message_id"])
                await msg.edit(embed=embed)
            except discord.NotFound:
                pass


# ---------------------------------------------------------------------
# Team Builder
# ---------------------------------------------------------------------

def parse_categories(text: str) -> list[dict]:
    """Parses 'Color: Black, White, Red; Size: S, M, L' into
    [{"category": "Color", "choices": ["Black", "White", "Red"], "images": {}}, ...]."""
    categories = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        name, choices_str = chunk.split(":", 1)
        choices = [c.strip() for c in choices_str.split(",") if c.strip()]
        if name.strip() and choices:
            categories.append({"category": name.strip(), "choices": choices, "images": {}})
    return categories


def build_team_embed(question: str, categories: list[dict], summary: dict, submitters: int) -> discord.Embed:
    embed = discord.Embed(
        title=question,
        description="Pick one option per category, then hit Submit.",
        color=discord.Color.dark_teal(),
    )
    for cat in categories:
        lines = []
        for choice in cat["choices"]:
            item_key = f"{cat['category']}:{choice}"
            count = summary.get(item_key, {}).get("selected", 0)
            pct = round(100 * count / submitters) if submitters else 0
            pic = " 🖼️" if cat.get("images", {}).get(choice) else ""
            lines.append(f"{choice}{pic} — {count} ({pct}%)")
        embed.add_field(name=cat["category"], value="\n".join(lines), inline=True)
    embed.set_footer(text=f"{submitters} submission(s)")
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, category: str, choices: list[str], images: dict, pending: dict):
        self.category = category
        self.images = images
        self.pending = pending
        options = [discord.SelectOption(label=c) for c in choices[:25]]
        super().__init__(placeholder=f"Choose {category}...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        self.pending.setdefault(interaction.user.id, {})[self.category] = choice

        image_embeds = []
        for url in self.images.get(choice, [])[:MAX_PREVIEW_EMBEDS]:
            e = discord.Embed(title=f"{self.category}: {choice}") if not image_embeds else discord.Embed()
            e.set_image(url=url)
            image_embeds.append(e)

        await interaction.response.send_message(
            f"Set **{self.category}** to **{choice}**. Pick the rest, then hit Submit.",
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

        for cat in categories[:4]:  # a View allows at most 5 rows; leave room for the submit button
            self.add_item(CategorySelect(cat["category"], cat["choices"], cat.get("images", {}), self.pending))
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


# ---------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------

class Builder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="budgetbuilder", description="Let people build a set of items within a budget")
    @app_commands.describe(
        title="Title for the build",
        budget="Maximum total price",
        items="Comma-separated 'Name:Price' pairs, e.g. 'Hoodie:30, Hat:15, Sticker:5'",
    )
    async def budgetbuilder(self, interaction: discord.Interaction, title: str, budget: float, items: str):
        item_list = parse_priced_items(items)
        if len(item_list) < 2:
            await interaction.response.send_message(
                "Give at least two comma-separated 'Name:Price' items, e.g. 'Hoodie:30, Hat:15'.",
                ephemeral=True,
            )
            return

        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        await interaction.response.send_message("Creating budget builder...")
        message = await interaction.original_response()

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="budget_builder",
            question=title,
            options=item_list,
        )

        view = BudgetBuilderView(post_id, title, budget, item_list)
        view.message = message
        embed = build_budget_embed(title, budget, item_list, {}, 0)
        await message.edit(content=None, embed=embed, view=view)

        labels = [i["label"] for i in item_list]
        results = await prompt_per_item_images(interaction, labels)
        if results:
            for item in item_list:
                if item["label"] in results:
                    item["image_urls"] = [url for url, _ in results[item["label"]]]
            await db.update_post_options(post_id, item_list)

            new_view = BudgetBuilderView(post_id, title, budget, item_list)
            new_view.message = message
            embed = build_budget_embed(title, budget, item_list, {}, 0)
            await message.edit(embed=embed, view=new_view)

    @app_commands.command(name="teambuilder", description="Let people pick one option per category (e.g. build an outfit)")
    @app_commands.describe(
        title="Title for the build",
        categories="Semicolon-separated categories, e.g. 'Color: Black, White, Red; Size: S, M, L'",
    )
    async def teambuilder(self, interaction: discord.Interaction, title: str, categories: str):
        category_list = parse_categories(categories)
        if len(category_list) < 1:
            await interaction.response.send_message(
                "Give at least one category, e.g. 'Color: Black, White, Red'.", ephemeral=True
            )
            return
        if len(category_list) > 4:
            await interaction.response.send_message(
                "Max 4 categories per team builder (Discord's component limit).", ephemeral=True
            )
            return

        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        await interaction.response.send_message("Creating team builder...")
        message = await interaction.original_response()

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

        labels = [f"{cat['category']}: {choice}" for cat in category_list for choice in cat["choices"]]
        results = await prompt_per_item_images(interaction, labels)
        if results:
            for cat in category_list:
                for choice in cat["choices"]:
                    key = f"{cat['category']}: {choice}"
                    if key in results:
                        cat["images"][choice] = [url for url, _ in results[key]]
            await db.update_post_options(post_id, category_list)

            new_view = TeamBuilderView(post_id, title, category_list)
            new_view.message = message
            embed = build_team_embed(title, category_list, {}, 0)
            await message.edit(embed=embed, view=new_view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Builder(bot))
