# 설정

ArBot의 모든 설정은 `configs/` 디렉토리의 YAML 파일로 관리됩니다.

## 설정 파일 구조

```
configs/
├── default.yaml       # 기본 시스템 설정
├── exchanges.yaml     # 거래소별 설정 (수수료, Rate Limit, WebSocket)
└── strategies.yaml    # 전략 파라미터
```

## default.yaml 상세 설명

### system - 시스템 기본 설정

```yaml
system:
  execution_mode: paper    # backtest | paper | live
  log_level: INFO          # DEBUG | INFO | WARNING | ERROR
  timezone: UTC
```

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `execution_mode` | 실행 모드. `backtest`(백테스팅), `paper`(가상 매매), `live`(실전 매매) | `paper` |
| `log_level` | 로그 레벨 | `INFO` |
| `timezone` | 시스템 타임존 | `UTC` |

### exchanges - 활성 거래소 목록

```yaml
exchanges:
  enabled:
    - binance
    - okx
    - bybit
    - upbit
    - kucoin
```

연결할 거래소를 리스트로 지정합니다. 각 거래소의 상세 설정은 `exchanges.yaml`에서 관리됩니다.

### symbols - 모니터링 대상 심볼

```yaml
symbols:
  - BTC/USDT
  - ETH/USDT
```

차익거래 기회를 모니터링할 거래 쌍 목록입니다. CCXT 표준 심볼 형식(`BASE/QUOTE`)을 사용합니다.

### detector - 차익거래 탐지 설정

#### Spatial Arbitrage (거래소 간 가격차 차익)

```yaml
detector:
  spatial:
    enabled: true
    min_spread_pct: 0.25     # 최소 스프레드 (%)
    min_depth_usd: 1000      # 최소 유동성 ($)
    max_latency_ms: 500      # 최대 허용 레이턴시 (ms)
```

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `enabled` | Spatial 전략 활성화 | `true` |
| `min_spread_pct` | 수수료 차감 후 최소 스프레드. 이 값 이상이어야 시그널 발생 | `0.25` |
| `min_depth_usd` | 해당 가격대의 최소 오더북 유동성 (USD) | `1000` |
| `max_latency_ms` | 데이터 수신 지연이 이 값을 초과하면 기회 무시 | `500` |

#### Triangular Arbitrage (삼각 차익)

```yaml
detector:
  triangular:
    enabled: true
    min_profit_pct: 0.15
    paths:
      - [BTC/USDT, ETH/BTC, ETH/USDT]
```

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `enabled` | Triangular 전략 활성화 | `true` |
| `min_profit_pct` | 최소 순이익률 (%) | `0.15` |
| `paths` | 삼각 차익 경로 (3개 거래 쌍 조합) | BTC-ETH |

#### Statistical Arbitrage (통계적 차익)

```yaml
detector:
  statistical:
    enabled: false             # Phase 2에서 활성화
    lookback_periods: 60       # Z-Score 계산 회고 기간
    entry_zscore: 2.0          # 진입 Z-Score 임계값
    exit_zscore: 0.5           # 청산 Z-Score 임계값
    p_value_threshold: 0.05    # 공적분 검정 p-value 임계값
```

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `enabled` | Statistical 전략 활성화 | `false` |
| `lookback_periods` | Z-Score 계산 시 사용할 과거 데이터 수 | `60` |
| `entry_zscore` | 이 Z-Score 이상이면 포지션 진입 | `2.0` |
| `exit_zscore` | 이 Z-Score 이하이면 포지션 청산 | `0.5` |
| `p_value_threshold` | Engle-Granger 공적분 검정의 유의 수준 | `0.05` |

### risk - 리스크 관리 설정

```yaml
risk:
  max_position_per_coin_usd: 10000
  max_position_per_exchange_usd: 50000
  max_total_exposure_usd: 100000
  max_daily_loss_usd: 500
  max_daily_loss_pct: 1.0
  max_drawdown_pct: 5.0
  price_deviation_threshold_pct: 10.0
  max_spread_pct: 5.0
  consecutive_loss_limit: 10
  cooldown_minutes: 30
  flash_crash_pct: 10.0
  spread_std_threshold: 3.0
  stale_threshold_seconds: 30.0
  warning_threshold_pct: 70.0
```

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `max_position_per_coin_usd` | 코인당 최대 포지션 (USD) | `10,000` |
| `max_position_per_exchange_usd` | 거래소당 최대 포지션 (USD) | `50,000` |
| `max_total_exposure_usd` | 전체 최대 노출 (USD) | `100,000` |
| `max_daily_loss_usd` | 일일 최대 허용 손실 (USD) | `500` |
| `max_daily_loss_pct` | 일일 최대 허용 손실률 (%) | `1.0` |
| `max_drawdown_pct` | 최대 드로다운 (%) | `5.0` |
| `price_deviation_threshold_pct` | 이상 가격 감지 임계값 (%) | `10.0` |
| `max_spread_pct` | 비정상 스프레드 임계값 (%) | `5.0` |
| `consecutive_loss_limit` | 연속 손실 횟수 제한 (초과 시 서킷 브레이커 작동) | `10` |
| `cooldown_minutes` | 서킷 브레이커 작동 후 대기 시간 (분) | `30` |
| `flash_crash_pct` | Flash Crash 감지 임계값 (%) | `10.0` |
| `spread_std_threshold` | 스프레드 표준편차 임계값 | `3.0` |
| `stale_threshold_seconds` | 데이터 유효성 만료 시간 (초) | `30.0` |
| `warning_threshold_pct` | 리스크 한도 경고 임계값 (%) | `70.0` |

### rebalancer - 리밸런싱 설정

```yaml
rebalancer:
  check_interval_minutes: 60
  imbalance_threshold_pct: 30
  min_transfer_usd: 100
  target_allocation: {}
  preferred_networks:
    USDT: [TRC20, SOL, Arbitrum]
    BTC: [Lightning, Bitcoin]
```

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `check_interval_minutes` | 잔고 체크 주기 (분) | `60` |
| `imbalance_threshold_pct` | 편중도가 이 값을 초과하면 리밸런싱 트리거 (%) | `30` |
| `min_transfer_usd` | 최소 전송 금액 (USD) | `100` |
| `target_allocation` | 거래소별 목표 배분 비율. 비어있으면 균등 분배 | `{}` |
| `preferred_networks` | 자산별 선호 전송 네트워크 (속도/수수료 기준 정렬) | USDT: TRC20, BTC: Lightning |

### alerts - 알림 설정

```yaml
alerts:
  telegram:
    enabled: true
    chat_id: ""
    bot_token: ""
  discord:
    enabled: false
    bot_token: ""
    guild_id: 0
    channel_id: 0
  thresholds:
    opportunity_min_pct: 0.5
    daily_pnl_alert: true
    error_alert: true
```

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `telegram.enabled` | Telegram 알림 활성화 | `true` |
| `telegram.chat_id` | Telegram 채팅 ID | (환경 변수에서 설정) |
| `telegram.bot_token` | Telegram 봇 토큰 | (환경 변수에서 설정) |
| `discord.enabled` | Discord 알림 활성화 | `false` |
| `thresholds.opportunity_min_pct` | 알림을 보낼 최소 기회 스프레드 (%) | `0.5` |
| `thresholds.daily_pnl_alert` | 일일 PnL 요약 알림 | `true` |
| `thresholds.error_alert` | 에러 발생 시 알림 | `true` |

### database - 데이터베이스 설정

```yaml
database:
  postgres:
    host: localhost
    port: 5432
    database: arbot
    user: arbot
    password: ""           # .env에서 로딩

  clickhouse:
    host: localhost
    port: 9000
    database: arbot

  redis:
    host: localhost
    port: 6379
    db: 0
```

::: tip
데이터베이스 비밀번호는 보안을 위해 `.env` 파일의 환경 변수(`POSTGRES_PASSWORD`, `REDIS_PASSWORD`)를 통해 설정하는 것을 권장합니다.
:::

## exchanges.yaml 상세 설명

각 거래소의 수수료, Rate Limit, WebSocket 설정을 관리합니다.

### 거래소 설정 구조

```yaml
exchanges:
  binance:
    tier: 1                          # 거래소 등급 (1: 핵심, 2: 보조)
    maker_fee_pct: 0.10              # Maker 수수료 (%)
    taker_fee_pct: 0.10              # Taker 수수료 (%)
    rate_limit:
      type: weight                   # Rate Limit 방식
      limit: 1200                    # 제한 값
      window: 60                     # 윈도우 (초)
    websocket:
      orderbook_depth: 10            # 오더북 수신 깊이
      reconnect_delay_s: 5           # 재연결 대기 시간 (초)
      max_reconnect_attempts: 10     # 최대 재연결 시도 횟수
```

### 거래소별 설정 요약

| 거래소 | Tier | Maker | Taker | Rate Limit 방식 | 제한 |
|--------|:----:|:-----:|:-----:|:------:|------|
| **Binance** | 1 | 0.10% | 0.10% | weight | 1200/60s |
| **OKX** | 1 | 0.08% | 0.10% | per_endpoint | 20/2s |
| **Bybit** | 1 | 0.10% | 0.10% | count | 600/5s |
| **Upbit** | 2 | 0.25% | 0.25% | count | 10/1s |
| **KuCoin** | 2 | 0.10% | 0.10% | count | 30/3s |
| **Bithumb** | 2 | 0.25% | 0.25% | count | 20/1s |
| **Gate.io** | 2 | 0.20% | 0.20% | count | 300/10s |
| **Bitget** | 2 | 0.10% | 0.10% | count | 20/1s |

### Rate Limit 방식

| 방식 | 설명 | 사용 거래소 |
|------|------|------------|
| `weight` | 엔드포인트별 가중치 합산. 전체 가중치가 제한 초과 시 차단 | Binance |
| `per_endpoint` | 엔드포인트별 독립 제한 | OKX |
| `count` | 단순 호출 횟수 제한 | Bybit, Upbit, KuCoin, Bithumb, Gate.io, Bitget |

### WebSocket 공통 설정

모든 거래소에 동일하게 적용되는 WebSocket 기본 설정:

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `orderbook_depth` | 수신할 오더북 레벨 수 (bid/ask 각각) | `10` |
| `reconnect_delay_s` | 연결 끊김 후 재연결 대기 시간 (초) | `5` |
| `max_reconnect_attempts` | 최대 재연결 시도 횟수. 초과 시 에러 알림 | `10` |

## 환경 변수 오버라이드

YAML 설정 값은 환경 변수로 오버라이드할 수 있습니다. `.env` 파일 또는 시스템 환경 변수를 통해 설정합니다.

### 주요 환경 변수

```bash
# 마스터 암호화 키
ARBOT_MASTER_KEY=your-secure-key

# 거래소 API 키
BINANCE_API_KEY=your-key
BINANCE_API_SECRET=your-secret
OKX_API_KEY=your-key
OKX_API_SECRET=your-secret
OKX_PASSPHRASE=your-passphrase
BYBIT_API_KEY=your-key
BYBIT_API_SECRET=your-secret
UPBIT_API_KEY=your-key
UPBIT_API_SECRET=your-secret
KUCOIN_API_KEY=your-key
KUCOIN_API_SECRET=your-secret
KUCOIN_PASSPHRASE=your-passphrase

# 데이터베이스
POSTGRES_PASSWORD=your-db-password
REDIS_PASSWORD=your-redis-password

# 알림
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

::: warning
환경 변수는 YAML 설정보다 우선합니다. 프로덕션 환경에서는 민감한 정보(API 키, 비밀번호)를 반드시 환경 변수로 관리하세요.
:::
