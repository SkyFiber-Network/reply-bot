import discord
from discord.ext import tasks

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

        c1 = client.get_channel(CHANNEL_1)
        c2 = client.get_channel(CHANNEL_2)

        messages.append(await c1.send(
            "**JOIN THIS SERVER**\nhttps://discord.gg/HJ9Z6wm8Vt\n@everyone"
        ))

        messages.append(await c1.send(
            "# **DM ME NOW IF YOU WANT ACCESS TO OUR SPECIAL TELEGRAM CHANNEL WITH OVER 3700+ POSTS? FOR FREE**\n\n@everyone"
        ))

        messages.append(await c2.send(
            "# **DM ME NOW FOR THE INVITE**\n@everyone"
        ))

    @client.event
    async def on_ready():
        if not promo.is_running():
            promo.start()

    return promo