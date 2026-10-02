# Discord DM Reply Bot

This bot replies to each direct message sent to it with the text in `REPLY_MESSAGE`. It does not reply in servers or to other bots.

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