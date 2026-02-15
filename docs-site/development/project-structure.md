# 프로젝트 구조

ArBot의 전체 디렉토리 구조와 각 모듈의 역할을 설명합니다.

## 전체 디렉토리 트리

```
arbot/
├── docs/                           # 프로젝트 문서
│   ├── PRD.md                      # 제품 요구사항 문서
│   ├── TRD.md                      # 기술 요구사항 문서
│   ├── TASKS.md                    # 개발 태스크 목록
│   └── AGENT_TEAM_PLAN.md          # 에이전트 팀 구성 계획
├── src/
│   └── arbot/                      # 메인 Python 패키지
│       ├── __init__.py
│       ├── main.py                 # 엔트리포인트
│       ├── config.py               # 설정 관리
│       ├── models/                 # 데이터 모델 (Pydantic)
│       ├── connectors/             # 거래소 커넥터
│       ├── detector/               # 기회 탐지
│       ├── execution/              # 주문 실행
│       ├── risk/                   # 리스크 관리
│       ├── backtest/               # 백테스팅
│       ├── rebalancer/             # 리밸런싱
│       ├── storage/                # 데이터 저장소
│       ├── dashboard/              # 대시보드 API
│       └── alerts/                 # 알림
├── dashboard/                      # 프론트엔드 (Next.js)
│   ├── package.json
│   └── src/
├── scripts/                        # 유틸리티 스크립트
│   ├── collect_historical.py       # 히스토리컬 데이터 수집
│   ├── init_db.py                  # DB 초기화
│   └── generate_report.py          # 리포트 생성
├── tests/                          # 테스트
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docker/                         # Docker 관련
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── prometheus.yml
├── configs/                        # 설정 파일
│   ├── default.yaml                # 기본 설정
│   ├── exchanges.yaml              # 거래소 설정
│   └── strategies.yaml             # 전략 파라미터
├── pyproject.toml                  # Python 프로젝트 설정
├── .env.example                    # 환경 변수 템플릿
└── .gitignore
```

## 핵심 모듈 (src/arbot/)

### connectors/ - 거래소 커넥터

거래소 WebSocket 및 REST API 연동을 담당합니다.

| 파일 | 역할 |
|------|------|
| `base.py` | 추상 커넥터 인터페이스 (BaseConnector) |
| `websocket_manager.py` | WebSocket 연결 관리 (재연결, 하트비트) |
| `rate_limiter.py` | 거래소별 Rate Limit 관리 (weight, count, token-bucket) |
| `binance.py` | Binance 커넥터 |
| `okx.py` | OKX 커넥터 |
| `bybit.py` | Bybit 커넥터 |
| `upbit.py` | Upbit 커넥터 (KRW 마켓) |
| `bithumb.py` | Bithumb 커넥터 (KRW 마켓) |
| `kucoin.py` | KuCoin 커넥터 |
| `gate.py` | Gate.io 커넥터 |
| `bitget.py` | Bitget 커넥터 |

### detector/ - 차익거래 기회 탐지

실시간 가격 데이터에서 차익거래 기회를 탐지합니다.

| 파일 | 역할 |
|------|------|
| `base.py` | 탐지 전략 인터페이스 |
| `spatial.py` | 거래소 간 가격차(Spatial Arbitrage) 탐지 |
| `triangular.py` | 단일 거래소 삼각 차익(Triangular Arbitrage) 탐지 |
| `statistical.py` | 공적분 기반 통계적 차익(Statistical Arbitrage) 탐지 |
| `spread_calculator.py` | 수수료/슬리피지 반영 순이익 스프레드 계산 |

### execution/ - 주문 실행

탐지된 차익 기회에 대한 주문 실행을 담당합니다.

| 파일 | 역할 |
|------|------|
| `base.py` | 실행 엔진 인터페이스 |
| `paper_executor.py` | 페이퍼 트레이딩 (가상 체결) |
| `live_executor.py` | 실전 트레이딩 (실제 주문) |
| `order_manager.py` | 주문 상태 관리 |
| `fill_simulator.py` | 체결 시뮬레이션 (오더북 기반) |

실행 모드는 `backtest`, `paper`, `live` 3가지로 구분됩니다.

### risk/ - 리스크 관리

거래 위험을 관리하고 자산을 보호합니다.

| 파일 | 역할 |
|------|------|
| `manager.py` | 리스크 관리 메인 엔진, 시그널 필터링 |
| `position_limits.py` | 코인별/거래소별/전체 포지션 한도 관리 |
| `drawdown_monitor.py` | 실시간 드로다운 모니터링 |
| `anomaly_detector.py` | Flash Crash, 비정상 스프레드 감지 |
| `circuit_breaker.py` | 연속 손실/이상 상황 시 자동 거래 중단 |

### backtest/ - 백테스팅

히스토리컬 데이터를 활용한 전략 검증 모듈입니다.

| 파일 | 역할 |
|------|------|
| `engine.py` | 이벤트 드리븐 백테스팅 엔진 |
| `data_loader.py` | ClickHouse에서 히스토리컬 데이터 로딩 |
| `simulator.py` | 오더북 기반 시장 시뮬레이션 (슬리피지, 부분 체결) |
| `metrics.py` | PnL, Sharpe Ratio, Max Drawdown, Win Rate 계산 |
| `report.py` | HTML/JSON 백테스트 리포트 생성 |

### rebalancer/ - 거래소 간 자금 리밸런싱

거래소 간 자금 편중을 감지하고 최적 이동 경로를 계산합니다.

| 파일 | 역할 |
|------|------|
| `monitor.py` | 다중 거래소 잔고 실시간 모니터링 |
| `optimizer.py` | 최적 자금 이동 경로/금액 계산 |
| `network_selector.py` | 전송 네트워크별 수수료/속도 비교, 최적 네트워크 선택 |
| `executor.py` | 리밸런싱 실행 (Phase 2: 알림, Phase 3: 자동 실행) |

### storage/ - 데이터 저장소

데이터베이스 접근 레이어입니다.

| 파일 | 역할 |
|------|------|
| `postgres.py` | PostgreSQL 비동기 드라이버 (asyncpg) - 거래 기록, 설정 |
| `clickhouse.py` | ClickHouse 드라이버 - 틱 데이터, 오더북 스냅샷 |
| `redis_cache.py` | Redis 캐시/Pub-Sub - 실시간 가격, 이벤트 스트림 |

### dashboard/ - 대시보드 API

웹 대시보드를 위한 REST API 및 WebSocket 서버입니다.

| 파일 | 역할 |
|------|------|
| `api.py` | REST API (시뮬레이션 결과, PnL, 포트폴리오) |
| `websocket_server.py` | WebSocket 서버 (실시간 가격, 시그널 스트리밍) |

### alerts/ - 알림

Telegram 등을 통한 알림 시스템입니다.

| 파일 | 역할 |
|------|------|
| `telegram.py` | Telegram 봇 알림 (기회 탐지, PnL, 에러) |
| `manager.py` | 알림 우선순위, 스로틀링, 중복 방지 |

### models/ - 데이터 모델

Pydantic 기반 데이터 모델입니다.

| 파일 | 역할 |
|------|------|
| `orderbook.py` | 오더북 데이터 모델 |
| `trade.py` | 거래/주문 데이터 모델 |
| `signal.py` | 차익거래 시그널 모델 |
| `balance.py` | 잔고 데이터 모델 |
| `config.py` | 설정 데이터 모델 |

## 설정 파일 (configs/)

| 파일 | 역할 |
|------|------|
| `default.yaml` | 시스템 기본 설정 (실행 모드, 로그 레벨, 심볼 목록) |
| `exchanges.yaml` | 거래소별 활성화/비활성화, 수수료 설정 |
| `strategies.yaml` | 전략 파라미터 (최소 스프레드, Z-Score 임계값 등) |

YAML 설정은 환경 변수로 오버라이드할 수 있습니다. Pydantic Settings로 설정 검증이 이루어집니다.

## 테스트 (tests/)

```
tests/
├── unit/           # 개별 함수/클래스 단위 테스트
├── integration/    # 모듈 간 상호작용 테스트
└── e2e/            # 전체 파이프라인 통합 테스트
```

자세한 내용은 [테스트 가이드](./testing.md)를 참조하세요.

## Docker 관련 (docker/)

| 파일 | 역할 |
|------|------|
| `Dockerfile` | ArBot 애플리케이션 컨테이너 이미지 빌드 |
| `docker-compose.yml` | 전체 인프라 서비스 구성 (PostgreSQL, ClickHouse, Redis, Grafana, Prometheus) |
| `prometheus.yml` | Prometheus 스크레이프 설정 |

자세한 내용은 [Docker 배포](../deployment/docker.md)를 참조하세요.
