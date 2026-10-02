import os

import discord


class ReplyBot(discord.Client):
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is not None or message.author.bot:
            return

        reply = os.environ["REPLY_MESSAGE"]
        await message.channel.send(reply)


def main() -> None:
    token = os.environ["DISCORD_TOKEN"]
    intents = discord.Intents.default()
    intents.dm_messages = True

    client = ReplyBot(intents=intents)
    client.run(token)


if __name__ == "__main__":
    main()