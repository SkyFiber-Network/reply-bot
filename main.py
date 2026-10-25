import asyncio
import os
import logging
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands
from hourly_promo import start_hourly_promo

logger = logging.getLogger(__name__)
LOG_CHANNEL_ID = 1555451943638532166
OWNER_USER_ID = 1485754261412380764


@dataclass(frozen=True)
class BackfillResult:
    scanned_messages: int
    replies_sent: int
    already_replied: int
    failed_replies: int
    unavailable_channels: int


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
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        @app_commands.command(
            name="test_reply",
            description="Send the configured automatic reply to yourself",
        )
        @app_commands.allowed_contexts(
            guilds=False,
            dms=True,
            private_channels=False,
        )
        async def test_reply(interaction: discord.Interaction) -> None:
            await self._test_reply_command(interaction)

        @app_commands.command(
            name="checkdms",
            description="Check the 100 newest cached DM messages and reply if needed",
        )
        @app_commands.allowed_contexts(
            guilds=False,
            dms=True,
            private_channels=False,
        )
        async def checkdms(interaction: discord.Interaction) -> None:
            await self._check_dms_command(interaction)

        self.tree.add_command(test_reply)
        self.tree.add_command(checkdms)
        await self.tree.sync()

    async def _is_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == OWNER_USER_ID:
            return True

        await interaction.response.send_message(
            "Only the bot owner can use this command."
        )
        return False

    async def _test_reply_command(self, interaction: discord.Interaction) -> None:
        if not await self._is_owner(interaction):
            return

        await interaction.response.send_message(os.environ["REPLY_MESSAGE"])
        await self._log_reply(interaction.user.id)

    async def _check_dms_command(self, interaction: discord.Interaction) -> None:
        if not await self._is_owner(interaction):
            return

        await interaction.response.defer()
        result = await self._catch_up_recent_dms(message_limit=100)
        await interaction.followup.send(
            f"Checked {result.scanned_messages} recent DM messages: "
            f"sent {result.replies_sent} replies, skipped "
            f"{result.already_replied} already-replied messages, "
            f"and failed to send {result.failed_replies} replies. "
            f"Could not read {result.unavailable_channels} DM channels."
        )

    async def _log_reply(self, user_id: int) -> None:
        try:
            channel = self.get_channel(self.log_channel_id)
            if channel is None:
                channel = await self.fetch_channel(self.log_channel_id)

            await channel.send(f"Sent automatic DM reply to user ID `{user_id}`.")
        except discord.DiscordException:
            logger.exception("Could not write DM reply log entry")

    async def _send_first_reply(
        self,
        message: discord.Message,
    ) -> Literal["already_replied", "failed", "replied"]:
        if not claim_first_reply(self.database_path, message.author.id):
            return "already_replied"

        try:
            await message.channel.send(os.environ["REPLY_MESSAGE"])
        except Exception:
            release_first_reply(self.database_path, message.author.id)
            logger.exception("Could not send automatic DM reply")
            return "failed"

        await self._log_reply(message.author.id)
        return "replied"

    async def _catch_up_recent_dms(
        self,
        channels: Sequence[discord.abc.PrivateChannel] | None = None,
        *,
        message_limit: int = 30,
    ) -> BackfillResult:
        if message_limit < 1:
            raise ValueError("message_limit must be at least 1")

        async with self._backfill_lock:
            recent_messages: list[discord.Message] = []
            unavailable_channels = 0
            for channel in self.private_channels if channels is None else channels:
                try:
                    async for message in channel.history(limit=message_limit):
                        if message.guild is None and not message.author.bot:
                            recent_messages.append(message)
                except discord.DiscordException:
                    unavailable_channels += 1
                    logger.exception("Could not read DM history")

            recent_messages.sort(key=lambda message: message.created_at)
            messages_to_check = recent_messages[-message_limit:]
            replies_sent = 0
            already_replied = 0
            failed_replies = 0
            for message in messages_to_check:
                result = await self._send_first_reply(message)
                if result == "replied":
                    replies_sent += 1
                elif result == "already_replied":
                    already_replied += 1
                else:
                    failed_replies += 1

            return BackfillResult(
                scanned_messages=len(messages_to_check),
                replies_sent=replies_sent,
                already_replied=already_replied,
                failed_replies=failed_replies,
                unavailable_channels=unavailable_channels,
            )

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