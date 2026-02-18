# TRD: ArBot - 기술 요구사항 문서

> **Version**: 1.0
> **Date**: 2026-02-08
> **Status**: Draft

---

## 1. 기술 스택 (Technology Stack)

### 1.1 언어 선택

| 컴포넌트 | 언어 | 근거 |
|----------|------|------|
| **코어 트레이딩 엔진** | Python 3.12+ | ccxt 라이브러리 생태계, 빠른 프로토타이핑, asyncio 지원 |
| **성능 크리티컬 모듈** | Rust (향후) | Go→Rust 전환 시 레이턴시 89μs→12μs (7x 개선 사례). GC 없음 |
| **대시보드/UI** | TypeScript (React/Next.js) | 실시간 차트, WebSocket 클라이언트 |
| **스크립트/자동화** | Python | 데이터 분석, 백테스팅 |

> **전략**: Phase 1~2는 Python으로 빠르게 프로토타이핑. Phase 3에서 성능 크리티컬 경로(가격 수집, 기회 탐지, 주문 실행)를 Rust로 점진 마이그레이션.

### 1.2 핵심 라이브러리/프레임워크

```
# 거래소 연동
ccxt              >= 4.0    # 100+ 거래소 통합 라이브러리
websockets        >= 12.0   # WebSocket 클라이언트
aiohttp           >= 3.9    # 비동기 HTTP 클라이언트

# 데이터 처리
numpy             >= 1.26   # 수치 연산
pandas            >= 2.2    # 시계열 데이터 처리
polars            >= 0.20   # 고성능 DataFrame (대용량 데이터)

# 통계/분석
statsmodels       >= 0.14   # 공적분 검정, 통계 모델링
scipy             >= 1.12   # 과학 계산

# 백테스팅
vectorbt          >= 0.26   # 벡터화 백테스팅

# 데이터 저장
asyncpg           >= 0.29   # PostgreSQL 비동기 드라이버
redis              >= 5.0   # 인메모리 캐시/메시지 브로커
clickhouse-driver >= 0.2    # 시계열 데이터 저장

# 모니터링/알림
prometheus-client >= 0.20   # 메트릭 수집
grafana                     # 대시보드 (외부)
python-telegram-bot >= 21   # 텔레그램 알림

# 보안
cryptography      >= 42.0   # API 키 암호화
```

### 1.3 인프라

```
Database:
  - PostgreSQL 16     : 거래 기록, 설정, 메타데이터
  - ClickHouse        : 틱 데이터, 오더북 스냅샷 (시계열 전용)
  - Redis 7           : 실시간 가격 캐시, Pub/Sub 메시지 브로커

Message Queue:
  - Redis Streams     : 내부 이벤트 전달 (가격 → 탐지 → 실행)

Monitoring:
  - Prometheus        : 메트릭 수집
  - Grafana           : 대시보드 시각화
  - Loki              : 로그 집계

Container:
  - Docker + Docker Compose : 로컬 개발/테스트
  - (Phase 3) Kubernetes    : 프로덕션 배포

Cloud (Phase 3):
  - AWS ap-northeast-1 (Tokyo) : Binance/Bybit 매칭엔진 근접
  - AWS ap-southeast-1 (Singapore) : OKX 근접
```

---

## 2. 시스템 아키텍처 (System Architecture)

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ArBot System                                 │
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
│  │  Exchange     │    │  Opportunity │    │  Execution   │          │
│  │  Connectors   │───▶│  Detector    │───▶│  Engine      │          │
│  │  (WebSocket)  │    │  (Analyzer)  │    │  (Trader)    │          │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘          │
│         │                   │                   │                   │
│         ▼                   ▼                   ▼                   │
│  ┌──────────────────────────────────────────────────────┐          │
│  │              Redis (Pub/Sub + Cache)                  │          │
│  └──────────────────────────────────────────────────────┘          │
│         │                   │                   │                   │
│         ▼                   ▼                   ▼                   │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
│  │  ClickHouse  │    │  PostgreSQL  │    │  Risk        │          │
│  │  (Tick Data) │    │  (Trades/    │    │  Manager     │          │
│  │              │    │   Config)    │    │              │          │
│  └──────────────┘    └──────────────┘    └──────┬───────┘          │
│                                                  │                   │
│  ┌──────────────┐    ┌──────────────┐           │                   │
│  │  Backtester  │    │  Dashboard   │◀──────────┘                   │
│  │              │    │  (Web UI)    │                                │
│  └──────────────┘    └──────────────┘                                │
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐                               │
│  │  Rebalancer  │    │  Alerting    │                               │
│  │              │    │  (Telegram)  │                               │
│  └──────────────┘    └──────────────┘                               │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 데이터 흐름 (Data Flow)

```
거래소 WebSocket ──▶ Exchange Connector ──▶ Normalizer ──▶ Redis Cache
                                                              │
                           ┌──────────────────────────────────┤
                           │                                  │
                           ▼                                  ▼
                    ClickHouse (저장)              Opportunity Detector
                                                              │
                                              ┌───────────────┤
                                              │               │
                                              ▼               ▼
                                      Risk Check        Signal Queue
                                              │               │
                                              └───────┬───────┘
                                                      │
                                                      ▼
                                        ┌─────────────────────────┐
                                        │    Execution Mode?      │
                                        ├─────────┬───────────────┤
                                        │ Paper   │    Live       │
                                        ▼         ▼               │
                                   Simulator  Order Executor      │
                                        │         │               │
                                        └─────┬───┘               │
                                              │                   │
                                              ▼                   │
                                        Trade Logger ──▶ PostgreSQL
                                              │
                                              ▼
                                      Dashboard / Alerts
```

---

## 3. 모듈 상세 설계 (Module Design)

### 3.1 Exchange Connector Module

```
arbot/
├── connectors/
│   ├── base.py              # 추상 커넥터 인터페이스
│   ├── websocket_manager.py # WebSocket 연결 관리 (재연결, 하트비트)
│   ├── rate_limiter.py      # 거래소별 Rate Limit 관리
│   ├── binance.py           # Binance 커넥터
│   ├── okx.py               # OKX 커넥터
│   ├── bybit.py             # Bybit 커넥터
│   ├── upbit.py             # Upbit 커넥터
│   ├── bithumb.py           # Bithumb 커넥터
│   ├── kucoin.py            # KuCoin 커넥터
│   ├── gate.py              # Gate.io 커넥터
│   └── bitget.py            # Bitget 커넥터
```

**BaseConnector 인터페이스**:
```python
class BaseConnector(ABC):
    """거래소 커넥터 추상 인터페이스"""

    @abstractmethod
    async def connect(self) -> None:
        """WebSocket 연결 수립"""

    @abstractmethod
    async def subscribe_orderbook(self, symbols: list[str], depth: int = 10) -> None:
        """오더북 스트림 구독"""

    @abstractmethod
    async def subscribe_trades(self, symbols: list[str]) -> None:
        """체결 스트림 구독"""

    @abstractmethod
    async def place_order(self, symbol: str, side: str, order_type: str,
                          quantity: float, price: float | None = None) -> Order:
        """주문 실행"""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """주문 취소"""

    @abstractmethod
    async def get_balances(self) -> dict[str, Balance]:
        """잔고 조회"""

    @abstractmethod
    async def get_withdrawal_fee(self, asset: str, network: str) -> float:
        """출금 수수료 조회"""
```

**Rate Limiter 설계**:
```python
# 거래소별 Rate Limit 정책
RATE_LIMITS = {
    "binance": {"type": "weight", "limit": 1200, "window": 60},
    "bybit":   {"type": "count",  "limit": 600,  "window": 5},
    "okx":     {"type": "per_endpoint", "default_limit": 20, "window": 2},
    "kraken":  {"type": "token_bucket", "capacity": 15, "refill_rate": 0.33},
    "upbit":   {"type": "count",  "limit": 10,   "window": 1},
}
```

### 3.2 Opportunity Detector Module

```
arbot/
├── detector/
│   ├── base.py                  # 탐지 전략 인터페이스
│   ├── spatial_detector.py      # 거래소 간 가격차 탐지
│   ├── triangular_detector.py   # 삼각 차익 탐지
│   ├── statistical_detector.py  # 통계적 차익 탐지
│   ├── spread_calculator.py     # 순이익 스프레드 계산기 (수수료 반영)
│   └── signal.py                # 차익거래 시그널 데이터 모델
```

**Spatial Arbitrage 탐지 로직**:
```python
@dataclass
class ArbitrageSignal:
    timestamp: float
    buy_exchange: str
    sell_exchange: str
    symbol: str
    buy_price: float
    sell_price: float
    gross_spread_pct: float    # 총 스프레드 (%)
    net_spread_pct: float      # 순 스프레드 (수수료 차감 후)
    estimated_profit: float    # 예상 수익 ($)
    confidence: float          # 신뢰도 (0~1)
    orderbook_depth: float     # 해당 가격대 유동성 ($)

def detect_spatial_opportunity(
    orderbooks: dict[str, OrderBook],
    fees: dict[str, TradingFee],
    min_spread_pct: float = 0.25,
    min_depth_usd: float = 1000.0,
) -> list[ArbitrageSignal]:
    """거래소 간 Spatial Arbitrage 기회 탐지"""
```

**Statistical Arbitrage 탐지 로직**:
```python
class StatisticalDetector:
    """공적분 기반 통계적 차익거래 탐지"""

    def find_cointegrated_pairs(
        self,
        price_matrix: pd.DataFrame,
        p_value_threshold: float = 0.05,
    ) -> list[CointegratedPair]:
        """Engle-Granger 테스트로 공적분 페어 탐색"""

    def calculate_zscore(
        self,
        spread: pd.Series,
        lookback: int = 60,
    ) -> float:
        """현재 스프레드의 Z-Score 계산"""

    def generate_signal(
        self,
        zscore: float,
        entry_threshold: float = 2.0,
        exit_threshold: float = 0.5,
    ) -> Signal:
        """Z-Score 기반 진입/청산 시그널 생성"""
```

### 3.3 Execution Engine Module

```
arbot/
├── execution/
│   ├── base.py              # 실행 엔진 인터페이스
│   ├── paper_executor.py    # 페이퍼 트레이딩 (시뮬레이션)
│   ├── live_executor.py     # 실전 트레이딩
│   ├── order_manager.py     # 주문 상태 관리
│   └── fill_simulator.py    # 체결 시뮬레이션 (오더북 기반)
```

**실행 모드 전환**:
```python
class ExecutionMode(Enum):
    BACKTEST = "backtest"     # 히스토리컬 데이터 기반
    PAPER = "paper"           # 실시간 데이터 + 가상 체결
    LIVE = "live"             # 실시간 데이터 + 실제 체결

class ExecutionEngine:
    def __init__(self, mode: ExecutionMode):
        self.executor = self._create_executor(mode)

    def _create_executor(self, mode: ExecutionMode) -> BaseExecutor:
        match mode:
            case ExecutionMode.BACKTEST:
                return BacktestExecutor()
            case ExecutionMode.PAPER:
                return PaperExecutor()
            case ExecutionMode.LIVE:
                return LiveExecutor()
```

### 3.4 Risk Manager Module

```
arbot/
├── risk/
│   ├── manager.py           # 리스크 관리 메인 엔진
│   ├── position_limits.py   # 포지션 크기 제한
│   ├── drawdown_monitor.py  # 드로다운 모니터링
│   ├── anomaly_detector.py  # 이상 가격 감지
│   └── circuit_breaker.py   # 서킷 브레이커 (긴급 정지)
```

**리스크 파라미터**:
```python
@dataclass
class RiskConfig:
    # 포지션 제한
    max_position_per_coin_usd: float = 10_000    # 코인당 최대 포지션
    max_position_per_exchange_usd: float = 50_000 # 거래소당 최대 포지션
    max_total_exposure_usd: float = 100_000       # 전체 최대 노출

    # 손실 제한
    max_daily_loss_usd: float = 500               # 일일 최대 손실
    max_daily_loss_pct: float = 1.0               # 일일 최대 손실률 (%)
    max_drawdown_pct: float = 5.0                 # 최대 드로다운 (%)

    # 이상 감지
    price_deviation_threshold_pct: float = 10.0   # 이상 가격 임계값
    max_spread_pct: float = 5.0                   # 비정상 스프레드 임계값

    # 서킷 브레이커
    consecutive_loss_limit: int = 10              # 연속 손실 시 정지
    cooldown_minutes: int = 30                    # 정지 후 대기 시간
```

### 3.5 Backtesting Module

```
arbot/
├── backtest/
│   ├── engine.py            # 백테스팅 엔진
│   ├── data_loader.py       # 히스토리컬 데이터 로더
│   ├── simulator.py         # 시장 시뮬레이터
│   ├── metrics.py           # 성과 지표 계산
│   └── report.py            # 리포트 생성
```

**백테스팅 흐름**:
```python
class BacktestEngine:
    async def run(self, config: BacktestConfig) -> BacktestResult:
        # 1. 데이터 로딩
        data = await self.data_loader.load(
            exchanges=config.exchanges,
            symbols=config.symbols,
            start=config.start_date,
            end=config.end_date,
        )

        # 2. 이벤트 루프
        for tick in data.iter_ticks():
            # 가격 업데이트
            self.market.update(tick)

            # 기회 탐지
            signals = self.detector.detect(self.market.state)

            # 리스크 체크
            approved = self.risk_manager.check(signals)

            # 시뮬레이션 실행
            for signal in approved:
                result = self.executor.execute(signal, self.market.orderbook)
                self.portfolio.update(result)

        # 3. 성과 계산
        return self.metrics.calculate(self.portfolio)
```

### 3.6 Rebalancer Module

```
arbot/
├── rebalancer/
│   ├── monitor.py           # 잔고 모니터링
│   ├── optimizer.py         # 최적 리밸런싱 경로 계산
│   ├── network_selector.py  # 최적 전송 네트워크 선택
│   └── executor.py          # 리밸런싱 실행
```

**네트워크 우선순위** (속도/수수료 기반):
```python
TRANSFER_NETWORKS = {
    "USDT": [
        {"network": "TRC20",     "fee": 1.0,  "confirm_min": 3},
        {"network": "SOL",       "fee": 0.1,  "confirm_min": 1},
        {"network": "Arbitrum",  "fee": 0.5,  "confirm_min": 2},
        {"network": "ERC20",     "fee": 5.0,  "confirm_min": 12},
    ],
    "BTC": [
        {"network": "Lightning", "fee": 0.01, "confirm_min": 0.1},
        {"network": "Bitcoin",   "fee": 15.0, "confirm_min": 30},
    ],
}
```

---

## 4. 데이터 모델 (Data Model)

### 4.1 PostgreSQL 스키마

```sql
-- 거래소 설정
CREATE TABLE exchanges (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) UNIQUE NOT NULL,
    tier            SMALLINT NOT NULL DEFAULT 2,  -- 1, 2, 3
    is_active       BOOLEAN NOT NULL DEFAULT true,
    maker_fee_pct   DECIMAL(6,4) NOT NULL,
    taker_fee_pct   DECIMAL(6,4) NOT NULL,
    config_json     JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 거래 기록
CREATE TABLE trades (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       UUID NOT NULL,
    exchange        VARCHAR(50) NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    side            VARCHAR(4) NOT NULL,          -- BUY / SELL
    order_type      VARCHAR(10) NOT NULL,         -- LIMIT / MARKET / IOC
    requested_qty   DECIMAL(20,8) NOT NULL,
    filled_qty      DECIMAL(20,8),
    requested_price DECIMAL(20,8),
    filled_price    DECIMAL(20,8),
    fee             DECIMAL(20,8),
    fee_asset       VARCHAR(10),
    status          VARCHAR(20) NOT NULL,          -- PENDING / FILLED / PARTIAL / CANCELLED / FAILED
    execution_mode  VARCHAR(10) NOT NULL,          -- BACKTEST / PAPER / LIVE
    latency_ms      DECIMAL(10,2),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    filled_at       TIMESTAMPTZ
);

-- 차익거래 시그널
CREATE TABLE arbitrage_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy        VARCHAR(20) NOT NULL,         -- SPATIAL / TRIANGULAR / STATISTICAL
    buy_exchange    VARCHAR(50),
    sell_exchange   VARCHAR(50),
    symbol          VARCHAR(20) NOT NULL,
    gross_spread    DECIMAL(10,6) NOT NULL,
    net_spread      DECIMAL(10,6) NOT NULL,
    estimated_pnl   DECIMAL(20,8),
    actual_pnl      DECIMAL(20,8),
    status          VARCHAR(20) NOT NULL,          -- DETECTED / EXECUTED / MISSED / REJECTED
    detected_at     TIMESTAMPTZ NOT NULL,
    executed_at     TIMESTAMPTZ,
    metadata_json   JSONB
);

-- 포트폴리오 스냅샷
CREATE TABLE portfolio_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    exchange        VARCHAR(50) NOT NULL,
    asset           VARCHAR(10) NOT NULL,
    balance         DECIMAL(20,8) NOT NULL,
    usd_value       DECIMAL(20,4) NOT NULL
);

-- 일일 성과
CREATE TABLE daily_performance (
    date            DATE NOT NULL,
    execution_mode  VARCHAR(10) NOT NULL,
    total_signals   INTEGER DEFAULT 0,
    executed_trades INTEGER DEFAULT 0,
    total_pnl       DECIMAL(20,8) DEFAULT 0,
    total_fees      DECIMAL(20,8) DEFAULT 0,
    net_pnl         DECIMAL(20,8) DEFAULT 0,
    sharpe_ratio    DECIMAL(10,4),
    max_drawdown    DECIMAL(10,6),
    win_rate        DECIMAL(6,4),
    PRIMARY KEY (date, execution_mode)
);
```

### 4.2 ClickHouse 스키마 (시계열 데이터)

```sql
-- 오더북 스냅샷
CREATE TABLE orderbook_snapshots (
    timestamp       DateTime64(3),
    exchange        LowCardinality(String),
    symbol          LowCardinality(String),
    bids            Array(Tuple(price Float64, qty Float64)),
    asks            Array(Tuple(price Float64, qty Float64)),
    mid_price       Float64,
    spread          Float64
) ENGINE = MergeTree()
ORDER BY (exchange, symbol, timestamp)
TTL timestamp + INTERVAL 90 DAY;

-- 체결 데이터
CREATE TABLE tick_trades (
    timestamp       DateTime64(3),
    exchange        LowCardinality(String),
    symbol          LowCardinality(String),
    price           Float64,
    quantity        Float64,
    side            LowCardinality(String)
) ENGINE = MergeTree()
ORDER BY (exchange, symbol, timestamp)
TTL timestamp + INTERVAL 180 DAY;

-- 스프레드 데이터 (사전 계산)
CREATE TABLE spread_history (
    timestamp       DateTime64(3),
    symbol          LowCardinality(String),
    exchange_a      LowCardinality(String),
    exchange_b      LowCardinality(String),
    price_a         Float64,
    price_b         Float64,
    spread_pct      Float64,
    net_spread_pct  Float64
) ENGINE = MergeTree()
ORDER BY (symbol, exchange_a, exchange_b, timestamp)
TTL timestamp + INTERVAL 365 DAY;
```

---

## 5. 프로젝트 디렉토리 구조

```
arbot/
├── docs/
│   ├── PRD.md
│   ├── TRD.md
│   ├── TASKS.md
│   └── AGENT_TEAM_PLAN.md
├── src/
│   └── arbot/
│       ├── __init__.py
│       ├── main.py                  # 엔트리포인트
│       ├── config.py                # 설정 관리
│       ├── models/                  # 데이터 모델 (Pydantic)
│       │   ├── __init__.py
│       │   ├── orderbook.py
│       │   ├── trade.py
│       │   ├── signal.py
│       │   ├── balance.py
│       │   └── config.py
│       ├── connectors/              # 거래소 커넥터
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── websocket_manager.py
│       │   ├── rate_limiter.py
│       │   ├── binance.py
│       │   ├── okx.py
│       │   ├── bybit.py
│       │   ├── upbit.py
│       │   ├── bithumb.py
│       │   ├── kucoin.py
│       │   ├── gate.py
│       │   └── bitget.py
│       ├── detector/                # 기회 탐지
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── spatial.py
│       │   ├── triangular.py
│       │   ├── statistical.py
│       │   └── spread_calculator.py
│       ├── execution/               # 주문 실행
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── paper_executor.py
│       │   ├── live_executor.py
│       │   ├── order_manager.py
│       │   └── fill_simulator.py
│       ├── risk/                    # 리스크 관리
│       │   ├── __init__.py
│       │   ├── manager.py
│       │   ├── position_limits.py
│       │   ├── drawdown_monitor.py
│       │   ├── anomaly_detector.py
│       │   └── circuit_breaker.py
│       ├── backtest/                # 백테스팅
│       │   ├── __init__.py
│       │   ├── engine.py
│       │   ├── data_loader.py
│       │   ├── simulator.py
│       │   ├── metrics.py
│       │   └── report.py
│       ├── rebalancer/              # 리밸런싱
│       │   ├── __init__.py
│       │   ├── monitor.py
│       │   ├── optimizer.py
│       │   ├── network_selector.py
│       │   └── executor.py
│       ├── storage/                 # 데이터 저장
│       │   ├── __init__.py
│       │   ├── postgres.py
│       │   ├── clickhouse.py
│       │   └── redis_cache.py
│       ├── dashboard/               # 대시보드 API
│       │   ├── __init__.py
│       │   ├── api.py
│       │   └── websocket_server.py
│       └── alerts/                  # 알림
│           ├── __init__.py
│           ├── telegram.py
│           └── manager.py
├── dashboard/                       # 프론트엔드 (Next.js)
│   ├── package.json
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   └── lib/
│   └── ...
├── scripts/                         # 유틸리티 스크립트
│   ├── collect_historical.py        # 히스토리컬 데이터 수집
│   ├── init_db.py                   # DB 초기화
│   └── generate_report.py           # 리포트 생성
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── configs/
│   ├── default.yaml                 # 기본 설정
│   ├── exchanges.yaml               # 거래소 설정
│   └── strategies.yaml              # 전략 파라미터
├── pyproject.toml
├── .env.example
└── .gitignore
```

---

## 6. 설정 관리 (Configuration)

### 6.1 기본 설정 (configs/default.yaml)

```yaml
system:
  execution_mode: paper        # backtest | paper | live
  log_level: INFO
  timezone: UTC

exchanges:
  enabled:
    - binance
    - okx
    - bybit
    - upbit
    - kucoin

symbols:
  - BTC/USDT
  - ETH/USDT

detector:
  spatial:
    enabled: true
    min_spread_pct: 0.25       # 최소 스프레드 (%)
    min_depth_usd: 1000        # 최소 유동성 ($)
    max_latency_ms: 500        # 최대 허용 레이턴시

  triangular:
    enabled: true
    min_profit_pct: 0.15
    paths:
      - [BTC/USDT, ETH/BTC, ETH/USDT]

  statistical:
    enabled: false             # Phase 2 활성화
    lookback_periods: 60
    entry_zscore: 2.0
    exit_zscore: 0.5
    p_value_threshold: 0.05

risk:
  max_position_per_coin_usd: 10000
  max_daily_loss_pct: 1.0
  max_drawdown_pct: 5.0
  consecutive_loss_limit: 10
  cooldown_minutes: 30

rebalancer:
  check_interval_minutes: 60
  imbalance_threshold_pct: 30  # 30% 이상 편중 시 리밸런싱
  preferred_networks:
    USDT: [TRC20, SOL, Arbitrum]
    BTC: [Lightning, Bitcoin]

alerts:
  telegram:
    enabled: true
    chat_id: ""
    bot_token: ""
  thresholds:
    opportunity_min_pct: 0.5
    daily_pnl_alert: true
    error_alert: true

database:
  postgres:
    host: localhost
    port: 5432
    database: arbot
    user: arbot
    password: ""               # .env에서 로딩

  clickhouse:
    host: localhost
    port: 9000
    database: arbot

  redis:
    host: localhost
    port: 6379
    db: 0
```

---

## 7. API 키 보안 관리

```python
# API 키는 .env 파일 + 런타임 암호화
# .env.example
ARBOT_MASTER_KEY=            # AES-256 암호화 마스터 키

BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_IP_WHITELIST=

OKX_API_KEY=
OKX_API_SECRET=
OKX_PASSPHRASE=

BYBIT_API_KEY=
BYBIT_API_SECRET=

UPBIT_API_KEY=
UPBIT_API_SECRET=

# DB
POSTGRES_PASSWORD=
REDIS_PASSWORD=

# 알림
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

> **보안 원칙**:
> - 거래 전용 API 키 (출금 권한 비활성화)
> - IP 화이트리스트 설정
> - API 키 AES-256 암호화 저장
> - .env 파일은 .gitignore에 포함

---

## 8. 성능 최적화 전략

### 8.1 Phase 1-2 (Python)
- `asyncio` 기반 비동기 I/O (WebSocket 동시 연결)
- `uvloop` 이벤트 루프 (2~4x 성능 향상)
- `orjson` JSON 파서 (표준 대비 10x 빠름)
- Redis 기반 인메모리 가격 캐시 (디스크 I/O 제거)
- `numpy` 벡터 연산 (루프 대신 배열 연산)

### 8.2 Phase 3 (Rust 마이그레이션 대상)
- WebSocket 메시지 파싱 → Rust
- 스프레드 계산 핫패스 → Rust
- 주문 생성/서명 → Rust
- Python에서 PyO3 바인딩으로 호출

---

## 9. 테스트 전략

| 레벨 | 범위 | 도구 |
|------|------|------|
| Unit | 개별 함수/클래스 | pytest |
| Integration | 모듈 간 상호작용 | pytest + testcontainers |
| E2E | 전체 파이프라인 (Backtest 모드) | pytest |
| Performance | 레이턴시/처리량 벤치마크 | pytest-benchmark |
| Exchange Mock | 거래소 API 시뮬레이션 | VCR.py + custom mock server |

---

## 10. 배포 (Deployment)

### 10.1 로컬 개발 (docker-compose)
```yaml
services:
  postgres:
    image: postgres:16
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]

  clickhouse:
    image: clickhouse/clickhouse-server:24
    ports: ["9000:9000", "8123:8123"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  arbot:
    build: .
    depends_on: [postgres, clickhouse, redis]
    env_file: .env
    volumes: [./configs:/app/configs]

  grafana:
    image: grafana/grafana:11
    ports: ["3000:3000"]

  prometheus:
    image: prom/prometheus:v2
    ports: ["9090:9090"]
```

### 10.2 프로덕션 (Phase 3)
- AWS EC2 (Tokyo/Singapore) 또는 전용 서버
- 매칭엔진 근접 배치로 네트워크 레이턴시 최소화
- 이중화 구성 (Primary + Failover)
