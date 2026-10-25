# Discord DM Reply Bot

This bot replies to the first direct message from each person with the text in `REPLY_MESSAGE`. It does not reply in servers or to other bots, and it will not reply to that person again.

## Set up the Discord bot

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application and add a bot.
2. Copy the bot token. Keep it private; if it is exposed, reset it in the Developer Portal.
3. Under **OAuth2**, generate an invite URL with the `bot` scope and the `Send Messages` permission, then invite the bot to your server if needed. Users can also DM the bot directly when they share a server or can otherwise access its profile.

## Deploy on Render

1. Push this project to a GitHub repository.
2. In Render, choose **New** > **Blueprint**, select the repository, and deploy the `render.yaml` web service.
3. In the service's **Environment** settings, set `DISCORD_TOKEN` to the bot token and `REPLY_MESSAGE` to the exact reply you want.
4. Save the environment variables and redeploy if prompted.

The service runs the Discord bot and exposes a small `/health` endpoint for Render health checks and external uptime monitors.

### Keep a free Render service awake

Free Render web services can spin down after 15 minutes without inbound requests. The bot serves `https://YOUR-SERVICE.onrender.com/health`; add this URL as an **HTTP(s)** monitor in UptimeRobot and use its most frequent available check interval. This inbound request can prevent inactivity spin-down, but it cannot prevent deploys, crashes, or other platform restarts. A free service kept awake also uses the workspace's monthly free instance hours.

## Reply history

The bot records replied-to Discord user IDs, known DM channel IDs, and a message checkpoint for each DM channel in a SQLite database. By default, the database is `reply_history.sqlite3` in the working directory. Configure `REPLY_DB_PATH` to a path on persistent storage for your hosting provider and keep that database across restarts and deployments; losing it resets both reply tracking and offline-DM checkpoints.

When the bot connects or reconnects, it checks up to 100 messages after the last saved checkpoints across recorded DM channels, replying to users not already in the reply history. `/checkdms` does the same and can be run again to continue if more than 100 messages are waiting. Failed sends do not advance the checkpoint past the failed message, so another check can retry it. The bot can only recover offline messages in channels it has recorded; it cannot discover every DM channel independently.

## Reply logs

After a DM reply is successfully sent, the bot posts a log entry containing the recipient's Discord user ID in the configured log channel. The channel ID is set in `main.py`. It does not copy the DM text into the log.

## Owner commands

The owner account configured by `OWNER_USER_ID` in `main.py` can use these slash commands in a direct message with the bot:

- `/test_reply` sends the configured reply to the owner and posts the usual log entry. It does not affect the owner's once-per-person reply history.
- `/checkdms` checks up to 100 messages since the last saved checkpoints across recorded DM channels and replies to users not already recorded in the reply history. The command reports how many messages were checked, replies sent or skipped, send failures, and channels whose history could not be read. If there are no recorded channels, DM the bot while it is online first so it can save the channel for future offline recovery.

Discord application commands can take some time to appear after deployment. Channel IDs are recorded as the bot encounters DMs. It cannot recover offline messages from a channel that was never recorded before the bot went offline, and the SQLite database must be on persistent storage for checkpoints to survive a redeploy.