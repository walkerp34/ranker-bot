"""
Discord modals and multi-selects can't accept file uploads - that's a
hard platform limit. The workaround used throughout this bot: ask the
creator to reply with an image (or a keyword like `skip`/`next`/`done`)
as a normal chat message, and capture it from there instead.

Two flows live here:
- prompt_multi_image: a flat "add more image(s) or type done" prompt,
  used by Rating for its single item.
- collect_named_options / collect_categories: the standardized
  name-then-image(s)-then-next-option flow used by Poll, Tier List,
  This or That, and Team Builder to build up their option/item/choice
  lists conversationally instead of via slash-command params.

All of it tries to delete each captured message so the channel doesn't
fill up with raw uploads/replies. That delete requires the bot's role
to have "Manage Messages" - without it, Discord silently blocks the
delete and the message just stays there; not fatal.
"""

import asyncio

import discord


async def _wait_for_reply(interaction: discord.Interaction, timeout: int):
    bot = interaction.client

    def check(m):
        return m.author.id == interaction.user.id and m.channel.id == interaction.channel_id

    try:
        message = await bot.wait_for("message", check=check, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    try:
        await message.delete()
    except discord.HTTPException:
        pass
    return message


async def prompt_multi_image(interaction: discord.Interaction, prompt_text: str, timeout: int = 60):
    """Repeatedly collects images from chat messages until the user types
    'done', sends something with no attachment, or nothing arrives within
    `timeout`. Returns a list of (url, bytes) tuples, upload order."""
    results = []
    await interaction.followup.send(prompt_text, ephemeral=True)

    while True:
        message = await _wait_for_reply(interaction, timeout)
        if message is None:
            break

        content = (message.content or "").strip().lower()
        if content == "done":
            break
        if not message.attachments:
            break

        attachment = message.attachments[0]
        data = await attachment.read()
        results.append((attachment.url, data))

    return results


async def _collect_item_images(interaction: discord.Interaction, name: str, timeout: int) -> list[str]:
    """After an option/item/choice's name is captured, prompts for its
    image(s) using the upload-or-skip / next pattern. Returns a list of
    image URLs (possibly empty)."""
    image_urls = []
    await interaction.followup.send(
        f"Upload an image for '{name}' now, or type `skip` to add it without one", ephemeral=True
    )
    have_one = False
    while True:
        message = await _wait_for_reply(interaction, timeout)
        if message is None:
            break

        content = (message.content or "").strip().lower()
        if not have_one and content == "skip":
            break
        if have_one and content == "next":
            break

        if message.attachments:
            image_urls.append(message.attachments[0].url)
            have_one = True
            await interaction.followup.send(
                "Another image for this one, or type `next` to move on?", ephemeral=True
            )
            continue

        if have_one:
            await interaction.followup.send(
                "Upload another image, or type `next` to move on.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"Upload an image for '{name}', or type `skip` to add it without one.", ephemeral=True
            )

    return image_urls


async def collect_named_options(
    interaction: discord.Interaction,
    kind_label: str,
    min_items: int = 2,
    max_items: int = 25,
    exact_items: int = None,
    timeout: int = 120,
) -> list[dict]:
    """Standardized one-at-a-time creation flow: ask for a name, then that
    item's image(s), then the next name, until `done` (or `exact_items` is
    reached). Used by Poll ("option"), Tier List ("item"), This or That
    ("side"), and Team Builder choices ("choice"). Returns
    [{"label": str, "image_urls": [str, ...]}, ...]."""
    items = []

    while True:
        index = len(items) + 1
        if exact_items:
            if index > exact_items:
                break
            prompt = f"What's the name of {kind_label} #{index} (of {exact_items})?"
        else:
            if index > max_items:
                break
            prompt = f"What's the name of {kind_label} #{index}?"
            if len(items) >= min_items:
                prompt += " (or type `done` to finish)"
        await interaction.followup.send(prompt, ephemeral=True)

        message = await _wait_for_reply(interaction, timeout)
        if message is None:
            break

        content = (message.content or "").strip()
        if not exact_items and content.lower() == "done":
            if len(items) >= min_items:
                break
            await interaction.followup.send(
                f"Need at least {min_items} {kind_label}s before finishing.", ephemeral=True
            )
            continue
        if not content:
            continue

        image_urls = await _collect_item_images(interaction, content, timeout)
        items.append({"label": content, "image_urls": image_urls})

    return items


async def collect_categories(
    interaction: discord.Interaction,
    max_categories: int = 4,
    timeout: int = 120,
) -> list[dict]:
    """Team Builder's two-level creation flow: repeatedly collects a
    category name, then walks through its choices one at a time (name,
    then image(s)) until `next category` moves on or `done` ends the
    whole builder. Categories with fewer than 2 choices are dropped.
    Returns
    [{"category": str, "choices": [{"label": str, "image_urls": [...]}]}]."""
    categories = []

    while len(categories) < max_categories:
        cat_index = len(categories) + 1
        prompt = f"What's the name of category #{cat_index}?"
        if categories:
            prompt += " (or type `done` to finish the team builder)"
        await interaction.followup.send(prompt, ephemeral=True)

        message = await _wait_for_reply(interaction, timeout)
        if message is None:
            break

        content = (message.content or "").strip()
        if categories and content.lower() == "done":
            break
        if not content:
            continue
        category_name = content

        choices = []
        while True:
            if not choices:
                await interaction.followup.send(
                    f"What's the name of the first choice in category '{category_name}'?",
                    ephemeral=True,
                )
            else:
                more_prompt = "Another choice in this category, or type `next category` to move on?"
                if len(choices) >= 2:
                    more_prompt += " (or `done` to finish the team builder)"
                await interaction.followup.send(more_prompt, ephemeral=True)

            cmsg = await _wait_for_reply(interaction, timeout)
            if cmsg is None:
                break

            ccontent = (cmsg.content or "").strip()
            lc = ccontent.lower()
            if choices and lc == "next category":
                break
            if choices and len(choices) >= 2 and lc == "done":
                categories.append({"category": category_name, "choices": choices})
                return categories
            if not ccontent:
                continue

            image_urls = await _collect_item_images(interaction, ccontent, timeout)
            choices.append({"label": ccontent, "image_urls": image_urls})

        if len(choices) < 2:
            await interaction.followup.send(
                f"Category '{category_name}' needs at least 2 choices — that category was dropped.",
                ephemeral=True,
            )
            continue

        categories.append({"category": category_name, "choices": choices})

    return categories
