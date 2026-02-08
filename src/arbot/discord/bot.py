"""ArBot Discord bot main class.

Integrates with the existing asyncio event loop via `start_bot()`.
Uses guild-scoped commands for instant slash command availability.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from arbot.alerts.discord_notifier import DiscordNotifier
from arbot.discord.cogs.info_commands import register_info_commands
from arbot.discord.cogs.trading_commands import register_trading_commands
from arbot.discord.context import BotContext

logger = logging.getLogger(__name__)


class ArBotDiscord(discord.Client):
    """Discord bot for ArBot interactive control and monitoring.

    Uses app_commands.CommandTree for slash commands, synced to a
    specific guild for instant availability.

    Args:
        bot_context: Bundle of references to core system components.
        discord_notifier: Notifier instance for alert channel setup.
        guild_id: Discord guild (server) ID for command scoping.
        channel_id: Discord channel ID for alert messages.
    """

    def __init__(
        self,
        bot_context: BotContext,
        discord_notifier: DiscordNotifier,
        guild_id: int,
        channel_id: int,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        super().__init__(intents=intents)

        self._bot_context = bot_context
        self._discord_notifier = discord_notifier
        self._guild_id = guild_id
        self._channel_id = channel_id
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Register commands and sync to guild.

        Called automatically by discord.py during client startup.
        Guild-scoped sync ensures commands appear immediately (no 1-hour wait).
        """
        register_info_commands(self.tree, self._bot_context)
        register_trading_commands(self.tree, self._bot_context)

        guild = discord.Object(id=self._guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info("Discord commands synced to guild %s", self._guild_id)

    async def on_ready(self) -> None:
        """Handle bot ready event.

        Resolves the alert channel and injects it into the DiscordNotifier.
        """
        logger.info("Discord bot ready: %s (ID: %s)", self.user, self.user.id if self.user else "?")

        channel = self.get_channel(self._channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self._channel_id)
            except discord.NotFound:
                logger.error("Discord alert channel %s not found", self._channel_id)
                return

        if isinstance(channel, discord.TextChannel):
            self._discord_notifier.set_channel(channel)
        else:
            logger.error(
                "Discord channel %s is not a text channel (type: %s)",
                self._channel_id,
                type(channel).__name__,
            )

    async def start_bot(self) -> None:
        """Start the bot within the current asyncio event loop.

        This wraps `self.start(token)` for use with `asyncio.create_task()`.
        Unlike `run()`, this does not create a new event loop.
        """
        token = self._bot_context.config.alerts.discord.bot_token
        await self.start(token)
