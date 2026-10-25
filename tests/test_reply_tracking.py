import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from main import (
    OWNER_USER_ID,
    ReplyBot,
    claim_first_reply,
    get_tracked_dm_channels,
    track_dm_channel,
)


class FakeDMChannel(MagicMock):
    def __init__(self, messages: list[discord.Message], channel_id: int = 98765) -> None:
        super().__init__(spec=discord.DMChannel)
        self.id = channel_id
        self.messages = messages
        self.send = AsyncMock()
        self.history_limit = None
        for message in messages:
            message.channel = self

    async def history(self, *, limit: int, after=None, oldest_first=None):
        self.history_limit = limit
        matching_messages = [
            message
            for message in self.messages
            if after is None or message.id > after.id
        ]
        for message in matching_messages[:limit]:
            yield message


class ReplyTrackingTests(unittest.TestCase):
    def test_only_first_contact_claims_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")

            self.assertTrue(claim_first_reply(database_path, 12345))
            self.assertFalse(claim_first_reply(database_path, 12345))

    def test_reply_history_survives_database_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")

            self.assertTrue(claim_first_reply(database_path, 12345))
            self.assertFalse(claim_first_reply(database_path, 12345))

    def test_users_are_tracked_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")

            self.assertTrue(claim_first_reply(database_path, 12345))
            self.assertTrue(claim_first_reply(database_path, 67890))


class ReplyLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_registers_and_syncs_owner_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=str(Path(temporary_directory) / "reply_history.sqlite3"),
                log_channel_id=24680,
            )
            bot.tree.sync = AsyncMock(return_value=[])

            await bot.setup_hook()

            self.assertEqual(
                {command.name for command in bot.tree.get_commands()},
                {"test_reply", "checkdms"},
            )
            bot.tree.sync.assert_awaited_once()

    async def test_test_reply_command_is_owner_only_and_does_not_mark_owner_replied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")
            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=database_path,
                log_channel_id=24680,
            )
            log_channel = MagicMock()
            log_channel.send = AsyncMock()
            bot.get_channel = MagicMock(return_value=log_channel)

            interaction = MagicMock()
            interaction.user.id = OWNER_USER_ID
            interaction.response.send_message = AsyncMock()

            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                await bot._test_reply_command(interaction)

            interaction.response.send_message.assert_awaited_once_with("Hello")
            log_channel.send.assert_awaited_once_with(
                f"Sent automatic DM reply to user ID `{OWNER_USER_ID}`."
            )
            self.assertTrue(claim_first_reply(database_path, OWNER_USER_ID))

    async def test_test_reply_command_rejects_non_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=str(Path(temporary_directory) / "reply_history.sqlite3"),
                log_channel_id=24680,
            )
            interaction = MagicMock()
            interaction.user.id = 12345
            interaction.response.send_message = AsyncMock()

            await bot._test_reply_command(interaction)

            interaction.response.send_message.assert_awaited_once_with(
                "Only the bot owner can use this command."
            )

    async def test_logs_only_after_first_reply_is_sent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")
            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=database_path,
                log_channel_id=24680,
            )
            log_channel = MagicMock()
            log_channel.send = AsyncMock()
            bot.get_channel = MagicMock(return_value=log_channel)

            message = MagicMock()
            message.id = 1
            message.guild = None
            message.author.bot = False
            message.author.id = 12345
            channel = FakeDMChannel([message], channel_id=54321)

            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                await bot.on_message(message)
                await bot.on_message(message)

            channel.send.assert_awaited_once_with("Hello")
            log_channel.send.assert_awaited_once_with(
                "Sent automatic DM reply to user ID `12345`."
            )

    async def test_catches_up_from_persisted_channel_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")
            base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
            messages = []
            for index in range(3):
                message = MagicMock()
                message.id = index + 1
                message.guild = None
                message.author.bot = False
                message.author.id = 1000 + index
                message.created_at = base_time + timedelta(minutes=index)
                messages.append(message)
            channel = FakeDMChannel(messages)
            track_dm_channel(database_path, channel.id, initial_message_id=1)

            self.assertTrue(claim_first_reply(database_path, 1002))
            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=database_path,
                log_channel_id=24680,
            )
            log_channel = MagicMock()
            log_channel.send = AsyncMock()
            bot.get_channel = MagicMock(return_value=log_channel)
            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                result = await bot._catch_up_recent_dms([channel])

            self.assertEqual(channel.send.await_count, 1)
            channel.send.assert_awaited_once_with("Hello")
            self.assertEqual(log_channel.send.await_count, 1)
            self.assertEqual(result.scanned_messages, 2)
            self.assertEqual(result.replies_sent, 1)
            self.assertEqual(result.already_replied, 1)
            self.assertEqual(result.tracked_channels, 1)
            self.assertEqual(dict(get_tracked_dm_channels(database_path))[channel.id], 3)

    async def test_checkdms_recovers_offline_messages_from_saved_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")
            messages = []
            base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for index in range(3):
                message = MagicMock()
                message.id = index + 11
                message.guild = None
                message.author.bot = False
                message.author.id = 2000 + index
                message.created_at = base_time + timedelta(minutes=index)
                messages.append(message)
            channel = FakeDMChannel(messages)
            track_dm_channel(database_path, channel.id, initial_message_id=10)
            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=database_path,
                log_channel_id=24680,
            )
            bot.fetch_channel = AsyncMock(return_value=channel)
            log_channel = MagicMock()
            log_channel.send = AsyncMock()
            bot.get_channel = MagicMock(return_value=log_channel)

            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                result = await bot._catch_up_recent_dms(message_limit=100)

            self.assertEqual(channel.history_limit, 100)
            self.assertEqual(result.scanned_messages, 3)
            self.assertEqual(result.replies_sent, 3)
            self.assertEqual(result.tracked_channels, 1)
            self.assertEqual(channel.send.await_count, 3)
            self.assertEqual(dict(get_tracked_dm_channels(database_path))[channel.id], 13)

    async def test_backfill_continues_after_global_100_message_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")
            channels = []
            for channel_index in range(2):
                messages = []
                for message_index in range(60):
                    message = MagicMock()
                    message.id = channel_index * 60 + message_index + 1
                    message.guild = None
                    message.author.bot = False
                    message.author.id = 3000 + channel_index * 60 + message_index
                    message.created_at = datetime.now(timezone.utc)
                    messages.append(message)
                channel = FakeDMChannel(
                    messages,
                    channel_id=60000 + channel_index,
                )
                track_dm_channel(database_path, channel.id)
                channels.append(channel)

            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=database_path,
                log_channel_id=24680,
            )
            bot.fetch_channel = AsyncMock(
                side_effect=lambda channel_id: channels[channel_id - 60000]
            )
            log_channel = MagicMock()
            log_channel.send = AsyncMock()
            bot.get_channel = MagicMock(return_value=log_channel)

            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                first_result = await bot._catch_up_recent_dms()
                second_result = await bot._catch_up_recent_dms()

            self.assertEqual(first_result.scanned_messages, 100)
            self.assertEqual(first_result.replies_sent, 100)
            self.assertEqual(second_result.scanned_messages, 20)
            self.assertEqual(second_result.replies_sent, 20)
            self.assertEqual([channel.send.await_count for channel in channels], [60, 60])

    async def test_failed_dm_send_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")
            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=database_path,
                log_channel_id=24680,
            )
            log_channel = MagicMock()
            log_channel.send = AsyncMock()
            bot.get_channel = MagicMock(return_value=log_channel)

            message = MagicMock()
            message.id = 1
            message.guild = None
            message.author.bot = False
            message.author.id = 12345
            channel = FakeDMChannel([message], channel_id=54321)
            channel.send.side_effect = [RuntimeError("send failed"), None]

            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                await bot.on_message(message)
                await bot.on_message(message)

            self.assertFalse(claim_first_reply(database_path, 12345))
            self.assertEqual(channel.send.await_count, 2)
            log_channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()