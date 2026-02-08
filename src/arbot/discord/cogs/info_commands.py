"""Information slash commands for ArBot Discord bot."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands

from arbot.discord.context import BotContext
from arbot.discord.views import PaginatorView, RefreshableView

ITEMS_PER_PAGE = 5


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def register_info_commands(tree: app_commands.CommandTree, ctx: BotContext) -> None:
    """Register all info slash commands on the command tree.

    Args:
        tree: Discord command tree to register commands on.
        ctx: Bot context with references to system components.
    """

    @tree.command(name="status", description="파이프라인 및 시뮬레이터 상태 조회")
    async def status_command(interaction: discord.Interaction) -> None:
        """Show pipeline and simulator status."""
        await _send_status(interaction, ctx)

    @tree.command(name="balance", description="거래소별 가상 잔고 조회")
    async def balance_command(interaction: discord.Interaction) -> None:
        """Show exchange balances."""
        portfolio = ctx.executor.get_portfolio()

        embed = discord.Embed(title="💰 거래소별 잔고", color=0x2ECC71)
        total_usd = 0.0

        for exchange_name, ex_balance in portfolio.exchange_balances.items():
            lines: list[str] = []
            ex_total = 0.0
            for asset_name, asset_bal in ex_balance.balances.items():
                usd_val = asset_bal.usd_value or 0.0
                lines.append(
                    f"`{asset_name}`: {asset_bal.free:,.4f}"
                    f" (${usd_val:,.2f})"
                )
                ex_total += usd_val
            total_usd += ex_total
            embed.add_field(
                name=f"📊 {exchange_name.upper()} (${ex_total:,.2f})",
                value="\n".join(lines) if lines else "잔고 없음",
                inline=False,
            )

        embed.set_footer(text=f"총 자산: ${total_usd:,.2f}")
        await interaction.response.send_message(embed=embed)

    @tree.command(name="signals", description="최근 탐지 시그널 목록")
    @app_commands.describe(page="페이지 번호 (기본: 1)")
    async def signals_command(
        interaction: discord.Interaction, page: int = 1
    ) -> None:
        """Show recent detected signals with pagination."""
        trade_log = ctx.pipeline.get_trade_log()

        if not trade_log:
            embed = discord.Embed(
                title="🔍 탐지 시그널", description="시그널이 없습니다.", color=0x95A5A6
            )
            await interaction.response.send_message(embed=embed)
            return

        # Build pages (most recent first)
        entries = list(reversed(trade_log))
        pages: list[discord.Embed] = []

        for i in range(0, len(entries), ITEMS_PER_PAGE):
            chunk = entries[i : i + ITEMS_PER_PAGE]
            page_num = i // ITEMS_PER_PAGE + 1
            total_pages = (len(entries) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

            embed = discord.Embed(
                title=f"🔍 탐지 시그널 ({page_num}/{total_pages})",
                color=0x3498DB,
            )
            for signal, buy_result, sell_result in chunk:
                detected = signal.detected_at.strftime("%H:%M:%S")
                embed.add_field(
                    name=f"{signal.symbol} | {detected}",
                    value=(
                        f"매수: {signal.buy_exchange} `${signal.buy_price:,.2f}`\n"
                        f"매도: {signal.sell_exchange} `${signal.sell_price:,.2f}`\n"
                        f"스프레드: `{signal.net_spread_pct:.3f}%` | "
                        f"상태: `{signal.status.value}`"
                    ),
                    inline=False,
                )
            pages.append(embed)

        # Clamp page
        page_idx = max(0, min(page - 1, len(pages) - 1))

        if len(pages) > 1:
            view = PaginatorView(pages)
            view._current = page_idx
            view._update_buttons()
            await interaction.response.send_message(embed=pages[page_idx], view=view)
        else:
            await interaction.response.send_message(embed=pages[0])

    @tree.command(name="trades", description="최근 체결 내역 조회")
    @app_commands.describe(page="페이지 번호 (기본: 1)")
    async def trades_command(
        interaction: discord.Interaction, page: int = 1
    ) -> None:
        """Show recent trade executions with pagination."""
        trade_log = ctx.pipeline.get_trade_log()

        if not trade_log:
            embed = discord.Embed(
                title="📊 체결 내역", description="체결 내역이 없습니다.", color=0x95A5A6
            )
            await interaction.response.send_message(embed=embed)
            return

        entries = list(reversed(trade_log))
        pages: list[discord.Embed] = []

        for i in range(0, len(entries), ITEMS_PER_PAGE):
            chunk = entries[i : i + ITEMS_PER_PAGE]
            page_num = i // ITEMS_PER_PAGE + 1
            total_pages = (len(entries) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

            embed = discord.Embed(
                title=f"📊 체결 내역 ({page_num}/{total_pages})",
                color=0x2ECC71,
            )
            for signal, buy_result, sell_result in chunk:
                pnl = (
                    sell_result.filled_price * sell_result.filled_quantity
                    - buy_result.filled_price * buy_result.filled_quantity
                    - buy_result.fee
                    - sell_result.fee
                )
                pnl_icon = "🟢" if pnl >= 0 else "🔴"
                embed.add_field(
                    name=f"{signal.symbol} | {pnl_icon} ${pnl:,.2f}",
                    value=(
                        f"매수: {buy_result.order.exchange} "
                        f"`${buy_result.filled_price:,.2f}` x "
                        f"`{buy_result.filled_quantity:.4f}`\n"
                        f"매도: {sell_result.order.exchange} "
                        f"`${sell_result.filled_price:,.2f}` x "
                        f"`{sell_result.filled_quantity:.4f}`\n"
                        f"수수료: `${buy_result.fee + sell_result.fee:,.4f}`"
                    ),
                    inline=False,
                )
            pages.append(embed)

        page_idx = max(0, min(page - 1, len(pages) - 1))

        if len(pages) > 1:
            view = PaginatorView(pages)
            view._current = page_idx
            view._update_buttons()
            await interaction.response.send_message(embed=pages[page_idx], view=view)
        else:
            await interaction.response.send_message(embed=pages[0])

    @tree.command(name="pnl", description="손익 요약 조회")
    async def pnl_command(interaction: discord.Interaction) -> None:
        """Show PnL summary."""
        stats = ctx.pipeline.get_stats()
        pnl_data = ctx.executor.get_pnl()

        net_pnl = stats.total_pnl_usd - stats.total_fees_usd
        pnl_icon = "📈" if net_pnl >= 0 else "📉"
        color = 0x2ECC71 if net_pnl >= 0 else 0xE74C3C

        embed = discord.Embed(title=f"{pnl_icon} 손익 요약", color=color)
        embed.add_field(
            name="총 PnL", value=f"`${stats.total_pnl_usd:,.2f}`", inline=True
        )
        embed.add_field(
            name="총 수수료", value=f"`${stats.total_fees_usd:,.2f}`", inline=True
        )
        embed.add_field(name="순 PnL", value=f"`${net_pnl:,.2f}`", inline=True)
        embed.add_field(
            name="체결 거래",
            value=f"`{stats.total_signals_executed}`",
            inline=True,
        )
        embed.add_field(
            name="실패 거래",
            value=f"`{stats.total_signals_failed}`",
            inline=True,
        )

        # Win rate
        total = stats.total_signals_executed + stats.total_signals_failed
        win_rate = (
            stats.total_signals_executed / total * 100 if total > 0 else 0.0
        )
        embed.add_field(
            name="승률", value=f"`{win_rate:.1f}%`", inline=True
        )

        # Per-exchange PnL
        if pnl_data:
            lines: list[str] = []
            for exchange, assets in pnl_data.items():
                for asset, amount in assets.items():
                    if amount != 0:
                        lines.append(f"{exchange}/{asset}: `${amount:,.4f}`")
            if lines:
                embed.add_field(
                    name="거래소별 PnL",
                    value="\n".join(lines[:10]),
                    inline=False,
                )

        await interaction.response.send_message(embed=embed)

    @tree.command(name="spread", description="거래소별 현재 스프레드 조회")
    @app_commands.describe(symbol="조회할 심볼 (예: BTC/USDT)")
    async def spread_command(
        interaction: discord.Interaction, symbol: str | None = None
    ) -> None:
        """Show current spreads across exchanges."""
        orderbooks = ctx.executor._orderbooks

        if not orderbooks:
            embed = discord.Embed(
                title="📉 스프레드",
                description="오더북 데이터가 없습니다.",
                color=0x95A5A6,
            )
            await interaction.response.send_message(embed=embed)
            return

        embed = discord.Embed(title="📉 거래소별 스프레드", color=0x3498DB)

        # Group by symbol
        symbols: dict[str, list[tuple[str, float, float, float]]] = {}
        for key, ob in orderbooks.items():
            if symbol and ob.symbol != symbol:
                continue
            sym = ob.symbol
            if sym not in symbols:
                symbols[sym] = []
            symbols[sym].append((
                ob.exchange,
                ob.best_bid,
                ob.best_ask,
                ob.spread_pct,
            ))

        for sym, exchanges in symbols.items():
            lines: list[str] = []
            exchanges.sort(key=lambda x: x[1], reverse=True)
            for ex_name, bid, ask, spread_pct in exchanges:
                lines.append(
                    f"`{ex_name}`: Bid `${bid:,.2f}` / Ask `${ask:,.2f}` "
                    f"({spread_pct:.3f}%)"
                )
            embed.add_field(
                name=f"🪙 {sym}",
                value="\n".join(lines) if lines else "데이터 없음",
                inline=False,
            )

        if not symbols:
            embed.description = (
                f"'{symbol}' 심볼에 대한 데이터가 없습니다." if symbol else "데이터 없음"
            )

        await interaction.response.send_message(embed=embed)

    @tree.command(name="help", description="ArBot 커맨드 도움말")
    async def help_command(interaction: discord.Interaction) -> None:
        """Show command help."""
        embed = discord.Embed(
            title="📖 ArBot 커맨드 도움말",
            description="사용 가능한 슬래시 커맨드 목록입니다.",
            color=0x9B59B6,
        )
        embed.add_field(
            name="📊 조회 커맨드",
            value=(
                "`/status` - 파이프라인/시뮬레이터 상태\n"
                "`/balance` - 거래소별 가상 잔고\n"
                "`/signals [page]` - 최근 탐지 시그널\n"
                "`/trades [page]` - 최근 체결 내역\n"
                "`/pnl` - 손익 요약\n"
                "`/spread [symbol]` - 거래소별 스프레드\n"
                "`/help` - 이 도움말"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️ 제어 커맨드",
            value=(
                "`/start` - 시뮬레이터 시작\n"
                "`/stop` - 시뮬레이터 정지 (확인 필요)"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)


async def _send_status(interaction: discord.Interaction, ctx: BotContext) -> None:
    """Build and send the status embed (reusable for refresh)."""
    stats = ctx.pipeline.get_stats()
    is_running = ctx.simulator.is_running

    status_icon = "🟢 실행 중" if is_running else "🔴 정지됨"
    color = 0x2ECC71 if is_running else 0xE74C3C

    uptime = (datetime.now(timezone.utc) - stats.started_at).total_seconds()

    embed = discord.Embed(title=f"📡 시스템 상태 — {status_icon}", color=color)
    embed.add_field(name="가동 시간", value=f"`{_format_duration(uptime)}`", inline=True)
    embed.add_field(
        name="실행 모드",
        value=f"`{ctx.config.system.execution_mode.value}`",
        inline=True,
    )
    embed.add_field(
        name="사이클", value=f"`{stats.cycles_run:,}`", inline=True
    )
    embed.add_field(
        name="탐지 시그널",
        value=f"`{stats.total_signals_detected:,}`",
        inline=True,
    )
    embed.add_field(
        name="승인/거부",
        value=f"`{stats.total_signals_approved}` / `{stats.total_signals_rejected}`",
        inline=True,
    )
    embed.add_field(
        name="체결",
        value=f"`{stats.total_signals_executed}`",
        inline=True,
    )
    embed.add_field(
        name="PnL",
        value=f"`${stats.total_pnl_usd:,.2f}`",
        inline=True,
    )

    # Risk manager status
    rm = ctx.risk_manager
    cooldown_text = "⚠️ 쿨다운 중" if rm.is_in_cooldown else "✅ 정상"
    embed.add_field(
        name="리스크",
        value=(
            f"일일 PnL: `${rm.daily_pnl:,.2f}`\n"
            f"연속 손실: `{rm.consecutive_losses}`\n"
            f"상태: {cooldown_text}"
        ),
        inline=False,
    )

    async def _refresh(i: discord.Interaction) -> None:
        await _send_status(i, ctx)

    if interaction.response.is_done():
        await interaction.edit_original_response(
            embed=embed, view=RefreshableView(_refresh)
        )
    else:
        await interaction.response.send_message(
            embed=embed, view=RefreshableView(_refresh)
        )
