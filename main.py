import asyncio
import os
import logging
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

import discord
from hourly_promo import start_hourly_promo

logger = logging.getLogger(__name__)
LOG_CHANNEL_ID = 1555451943638532166


def claim_first_reply(database_path: str, user_id: int) -> bool:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS replied_users ("
                "user_id INTEGER PRIMARY KEY)"
            )
            result = connection.execute(
                "INSERT OR IGNORE INTO replied_users (user_id) VALUES (?)",
                (user_id,),
            )
            return result.rowcount == 1


def release_first_reply(database_path: str, user_id: int) -> None:
    with closing(sqlite3.connect(database_path)) as connection:
        with connection:
            connection.execute(
                "DELETE FROM replied_users WHERE user_id = ?",
                (user_id,),
            )


class ReplyBot(discord.Client):
    def __init__(
        self,
        *,
        database_path: str,
        log_channel_id: int,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.database_path = database_path
        self.log_channel_id = log_channel_id
        self._backfill_lock = asyncio.Lock()

    async def _log_reply(self, user_id: int) -> None:
        try:
            channel = self.get_channel(self.log_channel_id)
            if channel is None:
                channel = await self.fetch_channel(self.log_channel_id)

            await channel.send(f"Sent automatic DM reply to user ID `{user_id}`.")
        except discord.DiscordException:
            logger.exception("Could not write DM reply log entry")

    async def _send_first_reply(self, message: discord.Message) -> None:
        if not claim_first_reply(self.database_path, message.author.id):
            return

        try:
            await message.channel.send(os.environ["REPLY_MESSAGE"])
        except Exception:
            release_first_reply(self.database_path, message.author.id)
            logger.exception("Could not send automatic DM reply")
            return

        await self._log_reply(message.author.id)

    async def _catch_up_recent_dms(
        self,
        channels: Sequence[discord.abc.PrivateChannel] | None = None,
    ) -> None:
        async with self._backfill_lock:
            recent_messages: list[discord.Message] = []
            for channel in self.private_channels if channels is None else channels:
                try:
                    async for message in channel.history(limit=30):
                        if message.guild is None and not message.author.bot:
                            recent_messages.append(message)
                except discord.DiscordException:
                    logger.exception("Could not read DM history")

            recent_messages.sort(
                key=lambda message: message.created_at,
                reverse=True,
            )
            for message in reversed(recent_messages[:30]):
                await self._send_first_reply(message)

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is not None or message.author.bot:
            return

        await self._send_first_reply(message)


def main() -> None:
    token = os.environ["DISCORD_TOKEN"]
    intents = discord.Intents.default()
    intents.dm_messages = True

    database_path = os.environ.get("REPLY_DB_PATH", "reply_history.sqlite3")
    client = ReplyBot(
        intents=intents,
        database_path=database_path,
        log_channel_id=LOG_CHANNEL_ID,
    )

    start_hourly_promo(client)
    promo_on_ready = client.on_ready

    @client.event
    async def on_ready() -> None:
        await client._catch_up_recent_dms()
        await promo_on_ready()

    client.run(token)


if __name__ == "__main__":
    main()