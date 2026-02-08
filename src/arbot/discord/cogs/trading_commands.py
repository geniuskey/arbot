"""Trading control slash commands for ArBot Discord bot."""

from __future__ import annotations

import discord
from discord import app_commands

from arbot.discord.context import BotContext
from arbot.discord.views import ConfirmStopView


def register_trading_commands(tree: app_commands.CommandTree, ctx: BotContext) -> None:
    """Register trading control slash commands on the command tree.

    Args:
        tree: Discord command tree to register commands on.
        ctx: Bot context with references to system components.
    """

    @tree.command(name="start", description="시뮬레이터 시작")
    async def start_command(interaction: discord.Interaction) -> None:
        """Start the paper trading simulator."""
        if ctx.simulator.is_running:
            await interaction.response.send_message(
                "⚠️ 시뮬레이터가 이미 실행 중입니다.", ephemeral=True
            )
            return

        await ctx.simulator.start()
        embed = discord.Embed(
            title="▶️ 시뮬레이터 시작됨",
            description="페이퍼 트레이딩 시뮬레이터가 시작되었습니다.",
            color=0x2ECC71,
        )
        await interaction.response.send_message(embed=embed)

    @tree.command(name="stop", description="시뮬레이터 정지")
    async def stop_command(interaction: discord.Interaction) -> None:
        """Stop the paper trading simulator with confirmation."""
        if not ctx.simulator.is_running:
            await interaction.response.send_message(
                "⚠️ 시뮬레이터가 실행 중이 아닙니다.", ephemeral=True
            )
            return

        confirm_view = ConfirmStopView()
        await interaction.response.send_message(
            "⚠️ 시뮬레이터를 정지하시겠습니까?",
            view=confirm_view,
        )

        timed_out = await confirm_view.wait()

        if timed_out or not confirm_view.confirmed:
            if timed_out:
                await interaction.edit_original_response(
                    content="⏰ 시간이 초과되었습니다. 정지가 취소됩니다.",
                    view=None,
                )
            return

        # User confirmed — stop the simulator
        await ctx.simulator.stop()
        report = ctx.simulator.get_report()

        embed = discord.Embed(
            title="⏹️ 시뮬레이터 정지됨",
            color=0xE74C3C,
        )
        embed.add_field(
            name="실행 시간",
            value=f"`{report.duration_seconds:.0f}초`",
            inline=True,
        )
        embed.add_field(
            name="사이클",
            value=f"`{report.pipeline_stats.cycles_run:,}`",
            inline=True,
        )
        embed.add_field(
            name="체결 거래",
            value=f"`{report.trade_count}`",
            inline=True,
        )

        net_pnl = report.final_pnl_usd - report.total_fees_usd
        pnl_icon = "📈" if net_pnl >= 0 else "📉"
        embed.add_field(
            name=f"{pnl_icon} 순 PnL",
            value=f"`${net_pnl:,.2f}`",
            inline=True,
        )
        embed.add_field(
            name="승률",
            value=f"`{report.win_rate:.1%}`",
            inline=True,
        )
        embed.add_field(
            name="총 수수료",
            value=f"`${report.total_fees_usd:,.2f}`",
            inline=True,
        )

        await interaction.edit_original_response(
            content=None, embed=embed, view=None
        )
