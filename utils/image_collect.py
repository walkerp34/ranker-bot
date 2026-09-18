"""
Discord modals (popup forms) and multi-selects can't accept file
uploads - that's a hard platform limit. The workaround used throughout
this bot: after a post is created, ask the creator to upload image(s)
as normal chat messages, and capture them from there instead.

Both helpers here support MULTIPLE images (not just one), and try to
delete each upload message once it's captured, so the channel doesn't
fill up with the raw uploads. That delete requires the bot's role to
have the "Manage Messages" permission in the server - without it,
Discord silently blocks the delete and the message just stays there.
"""

import asyncio

import discord


async def prompt_multi_image(interaction: discord.Interaction, prompt_text: str, timeout: int = 60):
    """Repeatedly collects images from chat messages until the user types
    'done', sends something with no attachment, or nothing arrives within
    `timeout`. Returns a list of (url, bytes) tuples, upload order."""
    bot = interaction.client
    results = []

    def check(m):
        return m.author.id == interaction.user.id and m.channel.id == interaction.channel_id

    await interaction.followup.send(prompt_text, ephemeral=True)

    while True:
        try:
            message = await bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            break

        content = (message.content or "").strip().lower()
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        if content == "done":
            break
        if not message.attachments:
            break

        attachment = message.attachments[0]
        data = await attachment.read()
        results.append((attachment.url, data))
        await interaction.followup.send(
            f"Got that image ({len(results)} so far). Upload another, or type `done` to finish.",
            ephemeral=True,
        )

    return results


async def prompt_per_item_images(interaction: discord.Interaction, labels: list[str], timeout: int = 60):
    """For each label in order: repeatedly collects images until the user
    types 'next' (move to the next label) or 'skip' (skip this label with no
    images). Typing 'done' stops the whole flow early. Returns
    {label: [(url, bytes), ...]} for labels that got at least one image."""
    bot = interaction.client
    results = {}

    def check(m):
        return m.author.id == interaction.user.id and m.channel.id == interaction.channel_id

    await interaction.followup.send(
        f"For each of the {len(labels)} item(s): upload one or more images, then type `next` "
        f"(or `skip` to skip it with no images). Type `done` anytime to stop early. "
        f"{timeout}s per message.",
        ephemeral=True,
    )

    for label in labels:
        images = []
        while True:
            try:
                message = await bot.wait_for("message", check=check, timeout=timeout)
            except asyncio.TimeoutError:
                break

            content = (message.content or "").strip().lower()
            try:
                await message.delete()
            except discord.HTTPException:
                pass

            if content == "done":
                if images:
                    results[label] = images
                return results
            if content in ("next", "skip"):
                break
            if message.attachments:
                attachment = message.attachments[0]
                data = await attachment.read()
                images.append((attachment.url, data))

        if images:
            results[label] = images

    return results


async def prompt_named_items(interaction: discord.Interaction, min_items: int = 2,
                              max_items: int = 25, timeout: int = 60):
    """The standardized creation flow: ask for an item's NAME as a chat
    message, then image(s) for it (upload any number, type 'next'/'skip' to
    move on), repeat. Typing 'done' instead of a name finishes early once
    at least `min_items` have been added. Returns (items, thumb_bytes):
    items is a list of {"label": str, "image_urls": [str, ...]}, and
    thumb_bytes is a {label: bytes} dict of each item's first image, for
    building an overview thumbnail grid."""
    bot = interaction.client
    items = []
    thumb_bytes = {}

    def check(m):
        return m.author.id == interaction.user.id and m.channel.id == interaction.channel_id

    await interaction.followup.send(
        f"Let's add items. For each: reply with its **name**, then upload image(s) for it "
        f"(or type `skip`), typing `next` once you're done with that item's images. "
        f"Type `done` instead of a name once you have at least {min_items} items. "
        f"{timeout}s per message.",
        ephemeral=True,
    )

    while len(items) < max_items:
        try:
            name_msg = await bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            break

        content = (name_msg.content or "").strip()
        try:
            await name_msg.delete()
        except discord.HTTPException:
            pass

        if content.lower() == "done":
            if len(items) >= min_items:
                break
            await interaction.followup.send(
                f"Need at least {min_items} items first — what's the name of item {len(items) + 1}?",
                ephemeral=True,
            )
            continue
        if not content:
            continue

        name = content
        image_urls = []
        await interaction.followup.send(
            f"Upload image(s) for **{name}**, or type `skip` to add it with no image.",
            ephemeral=True,
        )

        while True:
            try:
                img_msg = await bot.wait_for("message", check=check, timeout=timeout)
            except asyncio.TimeoutError:
                break

            content2 = (img_msg.content or "").strip().lower()
            try:
                await img_msg.delete()
            except discord.HTTPException:
                pass

            if content2 in ("skip", "next"):
                break
            if img_msg.attachments:
                attachment = img_msg.attachments[0]
                data = await attachment.read()
                image_urls.append(attachment.url)
                if name not in thumb_bytes:
                    thumb_bytes[name] = data
                await interaction.followup.send(
                    "Got it — another image for this item, or type `next`?", ephemeral=True
                )
            else:
                break

        items.append({"label": name, "image_urls": image_urls})
        if len(items) < max_items:
            await interaction.followup.send(
                f"Added **{name}** ({len(items)} item(s) so far). "
                f"What's the name of item {len(items) + 1}? (or type `done`, need {min_items} minimum)",
                ephemeral=True,
            )

    return items, thumb_bytes
