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
