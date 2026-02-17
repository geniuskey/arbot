"""Quick diagnostic: test Bybit v5 spot WebSocket orderbook subscription."""

import asyncio
import json

import websockets


async def main() -> None:
    url = "wss://stream.bybit.com/v5/public/spot"
    print(f"Connecting to {url}...")

    async with websockets.connect(url) as ws:
        print("Connected!")

        # Subscribe to orderbook depth 1 and 50
        sub_msg = {
            "op": "subscribe",
            "args": ["orderbook.1.BTCUSDT", "orderbook.50.BTCUSDT"],
        }
        await ws.send(json.dumps(sub_msg))
        print(f"Sent: {json.dumps(sub_msg)}")

        # Also subscribe to trades for comparison
        trade_msg = {
            "op": "subscribe",
            "args": ["publicTrade.BTCUSDT"],
        }
        await ws.send(json.dumps(trade_msg))
        print(f"Sent: {json.dumps(trade_msg)}")

        ob1_count = 0
        ob50_count = 0
        trade_count = 0
        other_count = 0

        for i in range(50):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(raw)

            if "op" in data:
                print(f"[{i}] Response: op={data.get('op')} success={data.get('success')} ret_msg={data.get('ret_msg')}")
                continue

            topic = data.get("topic", "")
            msg_type = data.get("type", "")

            if topic.startswith("orderbook.1."):
                ob1_count += 1
                if ob1_count <= 2:
                    d = data.get("data", {})
                    print(f"[{i}] OB1: type={msg_type} s={d.get('s')} bids={len(d.get('b', []))} asks={len(d.get('a', []))}")
            elif topic.startswith("orderbook.50."):
                ob50_count += 1
                if ob50_count <= 2:
                    d = data.get("data", {})
                    print(f"[{i}] OB50: type={msg_type} s={d.get('s')} bids={len(d.get('b', []))} asks={len(d.get('a', []))}")
            elif topic.startswith("publicTrade."):
                trade_count += 1
                if trade_count <= 2:
                    print(f"[{i}] Trade: {len(data.get('data', []))} trades")
            else:
                other_count += 1
                print(f"[{i}] Unknown: {json.dumps(data)[:200]}")

        print(f"\nSummary after 50 messages:")
        print(f"  orderbook.1:  {ob1_count}")
        print(f"  orderbook.50: {ob50_count}")
        print(f"  trades:       {trade_count}")
        print(f"  other:        {other_count}")


if __name__ == "__main__":
    asyncio.run(main())
