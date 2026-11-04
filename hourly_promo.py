import logging

import discord
from discord.ext import tasks

logger = logging.getLogger(__name__)

CHANNEL_1 = 1465490531537784934
CHANNEL_2 = 1452886322808098948

messages = []

def start_hourly_promo(client):

    @tasks.loop(hours=1)
    async def promo():
        for message in messages:
            try:
                await message.delete()
            except:
                pass

        messages.clear()

        async def send(channel_id, content):
            try:
                channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
                messages.append(await channel.send(content))
            except Exception:
                logger.exception("Hourly promo failed for channel %s", channel_id)

        await send(
            CHANNEL_1,
            "**JOIN THIS SERVER**\nhttps://discord.gg/HJ9Z6wm8Vt\n@everyone",
        )
        await send(
            CHANNEL_1,
            "# **DM ME NOW IF YOU WANT ACCESS TO OUR SPECIAL TELEGRAM CHANNEL WITH OVER 3700+ POSTS? FOR FREE**\n\n@everyone",
        )
        await send(
            CHANNEL_2,
            "# **DM ME NOW FOR THE INVITE**\n@everyone",
        )

    @client.event
    async def on_ready():
        if not promo.is_running():
            promo.start()

    return promo