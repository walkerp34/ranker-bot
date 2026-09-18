"""
Interactive Home hub.

A mod runs /homesetup once in a channel; it posts (and pins) a message
with buttons that create the most common post types without needing
to know slash commands. Buttons open a Modal (a small popup form) to
collect the text fields, then follow up asking for images the same
way the slash commands do.

Limitation: Discord modals only accept text input, not file uploads.
So the popup form itself is always text-only; images come from the
follow-up chat prompt right after.
"""

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks
from utils.image_collect import prompt_multi_image, prompt_per_item_images

from cogs.polls import PollView, build_results_embeds as build_poll_embeds
from cogs.tier_list import TierListView, build_results_embed as build_tier_embed, compose_grid
from cogs.rating import RatingView, build_results_embeds as build_rating_embeds


class PollModal(discord.ui.Modal, title="Create a Poll"):
    question = discord.ui.TextInput(label="Question", max_length=200)
    option1 = discord.ui.TextInput(label="Option 1", max_length=80)
    option2 = discord.ui.TextInput(label="Option 2", max_length=80)
    option3 = discord.ui.TextInput(label="Option 3 (optional)", max_length=80, required=False)
    option4 = discord.ui.TextInput(label="Option 4 (optional)", max_length=80, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        options = [o.value for o in [self.option1, self.option2, self.option3, self.option4] if o.value]

        await interaction.response.send_message("Creating poll...")
        message = await interaction.original_response()

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="poll",
            question=self.question.value,
            options=options,
        )
        view = PollView(post_id, self.question.value, options)
        embeds = build_poll_embeds(self.question.value, options, {})
        await message.edit(content=None, embeds=embeds, view=view)

        more = await prompt_multi_image(
            interaction,
            "Want to add image(s) to this poll? Upload them one at a time, "
            "type `done` when finished, or ignore to skip (60s per upload).",
        )
        if more:
            image_urls = [url for url, _ in more]
            view.image_urls = image_urls
            embeds = build_poll_embeds(self.question.value, options, {}, image_urls)
            await message.edit(embeds=embeds)


class TierListModal(discord.ui.Modal, title="Create a Tier List"):
    title_field = discord.ui.TextInput(label="Title", max_length=200)
    items_field = discord.ui.TextInput(
        label="Items (comma-separated)", placeholder="Banana, Apple, Cheese", max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        labels = [i.strip() for i in self.items_field.value.split(",") if i.strip()]
        if len(labels) < 2:
            await interaction.response.send_message("Give at least two comma-separated items.", ephemeral=True)
            return
        item_list = [{"label": label, "image_urls": []} for label in labels]

        await interaction.response.send_message("Creating tier list...")
        message = await interaction.original_response()

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="tier_list",
            question=self.title_field.value,
            options=item_list,
        )
        view = TierListView(post_id, self.title_field.value, item_list, has_grid=False)
        view.message = message
        embed = build_tier_embed(self.title_field.value, item_list, {}, has_grid=False)
        await message.edit(content=None, embed=embed, view=view)

        results = await prompt_per_item_images(interaction, labels)
        if results:
            thumb_bytes = {}
            for item in item_list:
                label = item["label"]
                if label in results:
                    item["image_urls"] = [url for url, _ in results[label]]
                    thumb_bytes[label] = results[label][0][1]
            await db.update_post_options(post_id, item_list)

            has_grid = len(thumb_bytes) > 0
            new_view = TierListView(post_id, self.title_field.value, item_list, has_grid)
            new_view.message = message
            embed = build_tier_embed(self.title_field.value, item_list, {}, has_grid)
            if has_grid:
                grid = compose_grid(list(thumb_bytes.items()))
                file = discord.File(grid, filename="tierlist_grid.png")
                await message.edit(embed=embed, attachments=[file], view=new_view)
            else:
                await message.edit(embed=embed, view=new_view)


class RatingModal(discord.ui.Modal, title="Create a Rating Post"):
    title_field = discord.ui.TextInput(label="What should people rate?", max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        allowed, reason = await checks.check_can_post(interaction)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        await interaction.response.send_message("Creating rating post...")
        message = await interaction.original_response()

        post_id = await db.create_post(
            message_id=message.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            mode="rating",
            question=self.title_field.value,
            options=["1", "2", "3", "4", "5"],
        )
        view = RatingView(post_id, self.title_field.value)
        embeds = build_rating_embeds(self.title_field.value, 0.0, 0)
        await message.edit(content=None, embeds=embeds, view=view)

        more = await prompt_multi_image(
            interaction,
            "Want to add image(s) to this rating post? Upload them one at a time, "
            "type `done` when finished, or ignore to skip (60s per upload).",
        )
        if more:
            image_urls = [url for url, _ in more]
            view.image_urls = image_urls
            embeds = build_rating_embeds(self.title_field.value, 0.0, 0, image_urls)
            await message.edit(embeds=embeds)


class HomeView(discord.ui.View):
    """Persistent-looking view for the hub message. Buttons open modals or
    show info; nothing here depends on message-specific state."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📊 Create Poll", style=discord.ButtonStyle.primary, custom_id="home_create_poll")
    async def create_poll(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PollModal())

    @discord.ui.button(label="🏆 Create Tier List", style=discord.ButtonStyle.primary, custom_id="home_create_tierlist")
    async def create_tierlist(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TierListModal())

    @discord.ui.button(label="⭐ Create Rating", style=discord.ButtonStyle.primary, custom_id="home_create_rating")
    async def create_rating(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RatingModal())

    @discord.ui.button(label="🕘 Recent Activity", style=discord.ButtonStyle.secondary, custom_id="home_recent_activity")
    async def recent_activity(self, interaction: discord.Interaction, button: discord.ui.Button):
        posts = await db.get_recent_posts(interaction.guild_id, limit=5)
        if not posts:
            await interaction.response.send_message("Nothing posted yet.", ephemeral=True)
            return
        lines = []
        for p in posts:
            link = f"https://discord.com/channels/{interaction.guild_id}/{p['channel_id']}/{p['message_id']}"
            lines.append(f"[{p['mode']}] {p['question']} — {link}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="ℹ️ More modes", style=discord.ButtonStyle.secondary, custom_id="home_more_modes")
    async def more_modes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Poll, Tier List, and Rating created here will ask if you want to upload images (as many "
            "as you like) right after you submit the form. This/or/That and the builders need a bit "
            "more input than a popup form supports — use their slash commands instead: "
            "`/thisorthat`, `/budgetbuilder`, `/teambuilder`.",
            ephemeral=True,
        )


class Home(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="homesetup", description="Post the Interactive Home hub in this channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def homesetup(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🏠 Interactive Home",
            description=(
                "Create something without typing a slash command:\n\n"
                "**📊 Create Poll** — quick poll, add images after\n"
                "**🏆 Create Tier List** — sort items into S/A/B/C/D/F, add images per item after\n"
                "**⭐ Create Rating** — collect 1-5 star ratings, add images after\n"
                "**🕘 Recent Activity** — see what's been posted lately\n\n"
                "Need This/or/That or budget/team builder? Tap **ℹ️ More modes** below."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=HomeView())
        message = await interaction.original_response()
        try:
            await message.pin()
        except discord.HTTPException:
            pass  # missing permission to pin, or too many pins already - not fatal

    @homesetup.error
    async def on_homesetup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission to set this up.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Home(bot))
    bot.add_view(HomeView())  # re-register the persistent view so buttons survive a restart
