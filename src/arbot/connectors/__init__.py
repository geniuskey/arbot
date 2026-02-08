"""Exchange connectors (WebSocket + REST).

Re-exports core connector classes:

    from arbot.connectors import BaseConnector, ConnectionState
    from arbot.connectors import WebSocketManager
    from arbot.connectors import RateLimiter, RateLimiterFactory, RateLimitPolicy
"""

from arbot.connectors.base import BaseConnector, ConnectionState
from arbot.connectors.rate_limiter import RateLimiter, RateLimiterFactory, RateLimitPolicy
from arbot.connectors.websocket_manager import WebSocketManager

__all__ = [
    "BaseConnector",
    "ConnectionState",
    "RateLimiter",
    "RateLimiterFactory",
    "RateLimitPolicy",
    "WebSocketManager",
]
