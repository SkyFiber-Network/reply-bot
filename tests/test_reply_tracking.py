import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from main import OWNER_USER_ID, ReplyBot, claim_first_reply


class FakeDMChannel:
    def __init__(self, messages: list[discord.Message]) -> None:
        self.messages = messages
        self.send = AsyncMock()
        self.history_limit = None
        for message in messages:
            message.channel = self

    async def history(self, *, limit: int):
        self.history_limit = limit
        for message in reversed(self.messages[-limit:]):
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
            message.guild = None
            message.author.bot = False
            message.author.id = 12345
            message.channel.send = AsyncMock()

            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                await bot.on_message(message)
                await bot.on_message(message)

            message.channel.send.assert_awaited_once_with("Hello")
            log_channel.send.assert_awaited_once_with(
                "Sent automatic DM reply to user ID `12345`."
            )

    async def test_catches_up_only_unreplied_users_in_latest_30_dms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")
            base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
            channels = []
            for index in range(31):
                message = MagicMock()
                message.guild = None
                message.author.bot = False
                message.author.id = 1000 + index
                message.created_at = base_time + timedelta(minutes=index)
                channels.append(FakeDMChannel([message]))

            self.assertTrue(claim_first_reply(database_path, 1030))
            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=database_path,
                log_channel_id=24680,
            )
            log_channel = MagicMock()
            log_channel.send = AsyncMock()
            bot.get_channel = MagicMock(return_value=log_channel)

            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                result = await bot._catch_up_recent_dms(channels)

            channels[0].send.assert_not_awaited()
            channels[30].send.assert_not_awaited()
            for channel in channels[1:30]:
                channel.send.assert_awaited_once_with("Hello")
            self.assertEqual(log_channel.send.await_count, 29)
            self.assertEqual(result.scanned_messages, 30)
            self.assertEqual(result.replies_sent, 29)
            self.assertEqual(result.already_replied, 1)

    async def test_checkdms_uses_latest_100_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "reply_history.sqlite3")
            messages = []
            base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for index in range(101):
                message = MagicMock()
                message.guild = None
                message.author.bot = False
                message.author.id = 2000 + index
                message.created_at = base_time + timedelta(minutes=index)
                messages.append(message)
            channel = FakeDMChannel(messages)
            bot = ReplyBot(
                intents=discord.Intents.default(),
                database_path=database_path,
                log_channel_id=24680,
            )
            log_channel = MagicMock()
            log_channel.send = AsyncMock()
            bot.get_channel = MagicMock(return_value=log_channel)

            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                result = await bot._catch_up_recent_dms(
                    [channel],
                    message_limit=100,
                )

            self.assertEqual(channel.history_limit, 100)
            self.assertEqual(result.scanned_messages, 100)
            self.assertEqual(result.replies_sent, 100)
            self.assertEqual(channel.send.await_count, 100)
            self.assertTrue(claim_first_reply(database_path, 2000))
            self.assertFalse(claim_first_reply(database_path, 2100))

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
            message.guild = None
            message.author.bot = False
            message.author.id = 12345
            message.channel.send = AsyncMock(
                side_effect=[RuntimeError("send failed"), None]
            )

            with patch.dict(os.environ, {"REPLY_MESSAGE": "Hello"}):
                await bot.on_message(message)
                await bot.on_message(message)

            self.assertFalse(claim_first_reply(database_path, 12345))
            self.assertEqual(message.channel.send.await_count, 2)
            log_channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()