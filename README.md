# Discord DM Reply Bot

This bot replies to the first direct message from each person with the text in `REPLY_MESSAGE`. It does not reply in servers or to other bots, and it will not reply to that person again.

## Set up the Discord bot

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application and add a bot.
2. Copy the bot token. Keep it private; if it is exposed, reset it in the Developer Portal.
3. Under **OAuth2**, generate an invite URL with the `bot` scope and the `Send Messages` permission, then invite the bot to your server if needed. Users can also DM the bot directly when they share a server or can otherwise access its profile.

## Deploy on Render

1. Push this project to a GitHub repository.
2. In Render, choose **New** > **Blueprint**, select the repository, and deploy the `render.yaml` worker.
3. In the worker's **Environment** settings, set `DISCORD_TOKEN` to the bot token and `REPLY_MESSAGE` to the exact reply you want.
4. Save the environment variables and redeploy if prompted.

The worker runs continuously while the Render service is active. No web server or port is needed.

## Reply history

The bot records Discord user IDs in a SQLite database so the once-per-person limit survives restarts. By default, the database is `reply_history.sqlite3` in the working directory. Set `REPLY_DB_PATH` to a path on NexusHosting's persistent storage if its default working directory is not preserved. Keep this database when updating or redeploying the bot; deleting it resets the reply history.

When the bot connects or reconnects, it checks the newest 30 incoming messages across its cached private channels and replies to people who are not yet in the reply history. Discord.py exposes at most 128 recent private channels, so this cannot recover messages from channels outside that cache or older than the checked messages. Failed sends are not recorded, allowing a later retry.

## Reply logs

After a DM reply is successfully sent, the bot posts a log entry containing the recipient's Discord user ID in the configured log channel. The channel ID is set in `main.py`. It does not copy the DM text into the log.