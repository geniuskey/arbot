"""WebSocket connection manager with automatic reconnection and heartbeat.

Provides a robust WebSocket client that handles:
- Automatic reconnection with exponential backoff
- Periodic heartbeat (ping/pong) to detect stale connections
- Channel subscription management
- Async message receive loop with callback dispatching
"""

import asyncio
import json
from collections.abc import Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from arbot.logging import get_logger


class WebSocketManager:
    """Manages a single WebSocket connection with auto-reconnect and heartbeat.

    Args:
        url: WebSocket server URL (e.g. "wss://stream.binance.com:9443/ws").
        on_message: Async callback invoked for each received message.
        reconnect_delay: Initial reconnection delay in seconds.
        max_reconnect_delay: Maximum reconnection delay in seconds (exponential backoff cap).
        heartbeat_interval: Interval between ping frames in seconds. Set to 0 to disable.
    """

    def __init__(
        self,
        url: str,
        on_message: Callable[[dict | str], Awaitable[None]],
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 60.0,
        heartbeat_interval: float = 30.0,
    ) -> None:
        self._url = url
        self._on_message = on_message
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._heartbeat_interval = heartbeat_interval

        self._ws: ClientConnection | None = None
        self._is_connected = False
        self._should_reconnect = True
        self._current_delay = reconnect_delay
        self._subscribed_channels: set[str] = set()
        self._msg_id: int = 0

        self._receive_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

        self._logger = get_logger("websocket_manager")

    @property
    def is_connected(self) -> bool:
        """Whether the WebSocket connection is currently active."""
        return self._is_connected

    async def connect(self) -> None:
        """Establish the WebSocket connection and start background loops.

        Raises:
            ConnectionError: If the initial connection fails after retries.
        """
        self._should_reconnect = True
        await self._connect()

    async def _connect(self) -> None:
        """Internal connection logic with error handling."""
        try:
            self._logger.info("websocket_connecting", url=self._url)
            self._ws = await websockets.connect(self._url)
            self._is_connected = True
            self._current_delay = self._reconnect_delay
            self._logger.info("websocket_connected", url=self._url)

            # Start background tasks
            self._receive_task = asyncio.create_task(self._receive_loop())
            if self._heartbeat_interval > 0:
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Re-subscribe to previously subscribed channels
            if self._subscribed_channels:
                channels = list(self._subscribed_channels)
                self._logger.info("websocket_resubscribing", channels=channels)
                await self.subscribe(channels)

        except (InvalidHandshake, InvalidURI, OSError) as e:
            self._is_connected = False
            self._logger.error("websocket_connect_failed", url=self._url, error=str(e))
            if self._should_reconnect:
                await self._reconnect()
            else:
                raise ConnectionError(f"WebSocket connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Gracefully close the WebSocket connection and stop background tasks."""
        self._should_reconnect = False
        self._is_connected = False

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._receive_task is not None:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._logger.info("websocket_disconnected", url=self._url)

    async def send(self, message: str | dict) -> None:
        """Send a message over the WebSocket connection.

        Args:
            message: A string or dict (will be JSON-serialized) to send.

        Raises:
            ConnectionError: If not currently connected.
        """
        if self._ws is None or not self._is_connected:
            raise ConnectionError("WebSocket is not connected")

        payload = json.dumps(message) if isinstance(message, dict) else message
        await self._ws.send(payload)
        self._logger.debug("websocket_sent", payload_length=len(payload))

    async def subscribe(self, channels: list[str]) -> None:
        """Subscribe to one or more channels.

        The channels are tracked internally so they can be re-subscribed
        after a reconnection.

        Args:
            channels: List of channel identifiers to subscribe to.
        """
        self._subscribed_channels.update(channels)
        if self._is_connected and self._ws is not None:
            self._msg_id += 1
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": channels,
                "id": self._msg_id,
            }
            await self.send(subscribe_msg)
            self._logger.info("websocket_subscribed", channels=channels)

    async def unsubscribe(self, channels: list[str]) -> None:
        """Unsubscribe from one or more channels.

        Args:
            channels: List of channel identifiers to unsubscribe from.
        """
        self._subscribed_channels.difference_update(channels)
        if self._is_connected and self._ws is not None:
            unsubscribe_msg = {
                "method": "UNSUBSCRIBE",
                "params": channels,
            }
            await self.send(unsubscribe_msg)
            self._logger.info("websocket_unsubscribed", channels=channels)

    async def _receive_loop(self) -> None:
        """Background loop that reads messages from the WebSocket."""
        if self._ws is None:
            return

        try:
            async for raw_message in self._ws:
                try:
                    if isinstance(raw_message, bytes):
                        raw_message = raw_message.decode("utf-8")
                    data = json.loads(raw_message)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = raw_message

                try:
                    await self._on_message(data)
                except Exception:
                    self._logger.exception("message_handler_error")

        except ConnectionClosed as e:
            self._is_connected = False
            self._logger.warning(
                "websocket_connection_closed",
                code=e.code,
                reason=str(e.reason),
            )
            if self._should_reconnect:
                await self._reconnect()

        except asyncio.CancelledError:
            raise

        except Exception:
            self._is_connected = False
            self._logger.exception("websocket_receive_error")
            if self._should_reconnect:
                await self._reconnect()

    async def _heartbeat_loop(self) -> None:
        """Background loop that sends periodic ping frames."""
        try:
            while self._is_connected and self._ws is not None:
                await asyncio.sleep(self._heartbeat_interval)
                if self._ws is not None and self._is_connected:
                    try:
                        pong = await self._ws.ping()
                        await asyncio.wait_for(pong, timeout=10.0)
                        self._logger.debug("websocket_heartbeat_ok")
                    except (asyncio.TimeoutError, ConnectionClosed):
                        self._logger.warning("websocket_heartbeat_failed")
                        self._is_connected = False
                        if self._should_reconnect:
                            await self._reconnect()
                        return
        except asyncio.CancelledError:
            raise

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        # Cancel existing background tasks before reconnecting
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        while self._should_reconnect:
            self._logger.info(
                "websocket_reconnecting",
                delay_s=self._current_delay,
                url=self._url,
            )
            await asyncio.sleep(self._current_delay)

            # Exponential backoff
            self._current_delay = min(
                self._current_delay * 2,
                self._max_reconnect_delay,
            )

            try:
                await self._connect()
                return
            except Exception:
                self._logger.exception("websocket_reconnect_failed")
