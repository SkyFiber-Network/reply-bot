import asyncio
import os
import logging
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aiohttp import web
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
    tracked_channels: int


def track_dm_channel(
    database_path: str,
    channel_id: int,
    initial_message_id: int = 0,
) -> bool:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS dm_channels ("
                "channel_id INTEGER PRIMARY KEY, "
                "last_message_id INTEGER NOT NULL)"
            )
            result = connection.execute(
                "INSERT OR IGNORE INTO dm_channels "
                "(channel_id, last_message_id) VALUES (?, ?)",
                (channel_id, initial_message_id),
            )
            return result.rowcount == 1


def get_tracked_dm_channels(database_path: str) -> list[tuple[int, int]]:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS dm_channels ("
            "channel_id INTEGER PRIMARY KEY, "
            "last_message_id INTEGER NOT NULL)"
        )
        return connection.execute(
            "SELECT channel_id, last_message_id FROM dm_channels"
        ).fetchall()


def update_dm_channel_checkpoint(
    database_path: str,
    channel_id: int,
    message_id: int,
) -> None:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection:
        with connection:
            connection.execute(
                "UPDATE dm_channels SET last_message_id = ? "
                "WHERE channel_id = ? AND last_message_id < ?",
                (message_id, channel_id, message_id),
            )


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
        self._health_runner: web.AppRunner | None = None

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
            description="Check for missed messages in tracked DMs",
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
        await self._start_health_server()
        await self.tree.sync()

    async def _start_health_server(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._health_check)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(
            runner,
            host="0.0.0.0",
            port=int(os.environ.get("PORT", "8080")),
        )
        await site.start()
        self._health_runner = runner

    async def _health_check(self, request: web.Request) -> web.Response:
        return web.Response(text="OK")

    async def close(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
            self._health_runner = None
        await super().close()

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
        if result.tracked_channels == 0:
            await interaction.followup.send(
                "No DM channels are recorded yet. The bot must see a DM while "
                "online before it can track that channel for offline catch-up."
            )
            return

        await interaction.followup.send(
            f"Checked {result.scanned_messages} messages since the last check "
            f"across {result.tracked_channels} tracked DMs: "
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
        message_limit: int = 100,
    ) -> BackfillResult:
        if message_limit < 1:
            raise ValueError("message_limit must be at least 1")

        async with self._backfill_lock:
            unavailable_channels = 0
            if channels is not None:
                for channel in channels:
                    track_dm_channel(self.database_path, channel.id)
                tracked_checkpoints = dict(
                    get_tracked_dm_channels(self.database_path)
                )
                channels_to_scan = [
                    (channel, channel.id, tracked_checkpoints[channel.id])
                    for channel in channels
                ]
                tracked_channel_count = len(tracked_checkpoints)
            else:
                cached_channels = {
                    channel.id: channel for channel in self.private_channels
                }
                tracked_channels = get_tracked_dm_channels(self.database_path)
                tracked_channel_count = len(tracked_channels)
                tracked_ids = {channel_id for channel_id, _ in tracked_channels}
                for channel_id, channel in cached_channels.items():
                    if channel_id not in tracked_ids:
                        try:
                            latest_message = None
                            async for message in channel.history(limit=1):
                                latest_message = message
                            checkpoint = (
                                latest_message.id if latest_message is not None else 0
                            )
                            track_dm_channel(
                                self.database_path,
                                channel_id,
                                checkpoint,
                            )
                        except discord.DiscordException:
                            unavailable_channels += 1
                            logger.exception("Could not initialize DM history")

                tracked_channels = get_tracked_dm_channels(self.database_path)
                tracked_channel_count = len(tracked_channels)
                channels_to_scan = []
                for channel_id, checkpoint in tracked_channels:
                    channel = cached_channels.get(channel_id)
                    if channel is None:
                        try:
                            channel = await self.fetch_channel(channel_id)
                        except discord.DiscordException:
                            unavailable_channels += 1
                            logger.exception(
                                "Could not fetch tracked DM channel %s",
                                channel_id,
                            )
                            continue

                    if not isinstance(channel, discord.abc.PrivateChannel):
                        unavailable_channels += 1
                        logger.error(
                            "Tracked channel %s is not a private channel",
                            channel_id,
                        )
                        continue
                    channels_to_scan.append((channel, channel_id, checkpoint))

            pending_messages: list[tuple[int, discord.Message]] = []
            for channel, channel_id, checkpoint in channels_to_scan:
                try:
                    after = discord.Object(id=checkpoint) if checkpoint else None
                    async for message in channel.history(
                        limit=message_limit,
                        after=after,
                        oldest_first=True,
                    ):
                        pending_messages.append((channel_id, message))
                except discord.DiscordException:
                    unavailable_channels += 1
                    logger.exception("Could not read DM history for channel %s", channel_id)

            scanned_messages = 0
            replies_sent = 0
            already_replied = 0
            failed_replies = 0
            failed_channels: set[int] = set()
            pending_messages.sort(key=lambda item: item[1].id)
            for channel_id, message in pending_messages[:message_limit]:
                if channel_id in failed_channels:
                    continue

                scanned_messages += 1
                if message.guild is None and not message.author.bot:
                    result = await self._send_first_reply(message)
                    if result == "replied":
                        replies_sent += 1
                    elif result == "already_replied":
                        already_replied += 1
                    else:
                        failed_replies += 1
                        failed_channels.add(channel_id)
                        continue

                update_dm_channel_checkpoint(
                    self.database_path,
                    channel_id,
                    message.id,
                )

            return BackfillResult(
                scanned_messages=scanned_messages,
                replies_sent=replies_sent,
                already_replied=already_replied,
                failed_replies=failed_replies,
                unavailable_channels=unavailable_channels,
                tracked_channels=tracked_channel_count,
            )

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is not None or message.author.bot:
            return

        is_new_channel = track_dm_channel(
            self.database_path,
            message.channel.id,
            max(message.id - 1, 0),
        )
        if is_new_channel:
            result = await self._send_first_reply(message)
            if result != "failed":
                update_dm_channel_checkpoint(
                    self.database_path,
                    message.channel.id,
                    message.id,
                )
            return

        await self._catch_up_recent_dms([message.channel])


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