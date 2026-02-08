"""Reusable Discord UI components (buttons, views)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import discord


class RefreshButton(discord.ui.Button["RefreshableView"]):
    """Button that re-runs the parent command callback on click.

    Args:
        callback_fn: Async function to call when button is pressed.
    """

    def __init__(
        self,
        callback_fn: Callable[[discord.Interaction], Coroutine[Any, Any, None]],
    ) -> None:
        super().__init__(label="🔄 새로고침", style=discord.ButtonStyle.secondary)
        self._callback_fn = callback_fn

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle button click by invoking the refresh callback."""
        await self._callback_fn(interaction)


class RefreshableView(discord.ui.View):
    """View with a single refresh button.

    Args:
        callback_fn: Async function to call when refresh is pressed.
        timeout: View timeout in seconds.
    """

    def __init__(
        self,
        callback_fn: Callable[[discord.Interaction], Coroutine[Any, Any, None]],
        *,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.add_item(RefreshButton(callback_fn))


class PaginatorView(discord.ui.View):
    """Paginated view with Previous/Next buttons.

    Args:
        pages: List of embeds, one per page.
        timeout: View timeout in seconds.
    """

    def __init__(
        self,
        pages: list[discord.Embed],
        *,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self._pages = pages
        self._current = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        """Enable/disable buttons based on current page position."""
        self._prev_btn.disabled = self._current <= 0
        self._next_btn.disabled = self._current >= len(self._pages) - 1

    @property
    def current_embed(self) -> discord.Embed:
        """Return the embed for the current page."""
        return self._pages[self._current]

    @discord.ui.button(label="◀ 이전", style=discord.ButtonStyle.secondary)
    async def _prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[PaginatorView]
    ) -> None:
        """Go to previous page."""
        if self._current > 0:
            self._current -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.current_embed, view=self)

    @discord.ui.button(label="다음 ▶", style=discord.ButtonStyle.secondary)
    async def _next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[PaginatorView]
    ) -> None:
        """Go to next page."""
        if self._current < len(self._pages) - 1:
            self._current += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.current_embed, view=self)


class ConfirmStopView(discord.ui.View):
    """Confirmation dialog for stopping the simulator.

    Args:
        timeout: View timeout in seconds.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        super().__init__(timeout=timeout)
        self.confirmed: bool | None = None

    @discord.ui.button(label="⛔ 정지 확인", style=discord.ButtonStyle.danger)
    async def confirm_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[ConfirmStopView]
    ) -> None:
        """Confirm simulator stop."""
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button[ConfirmStopView]
    ) -> None:
        """Cancel stop operation."""
        self.confirmed = False
        self.stop()
        await interaction.response.edit_message(
            content="❌ 정지가 취소되었습니다.", embed=None, view=None
        )
