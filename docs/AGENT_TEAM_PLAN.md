# Agent Team Plan: ArBot 개발 에이전트 팀 구성

> **Version**: 1.0
> **Date**: 2026-02-08

---

## 1. 팀 구성 개요

ArBot 프로젝트는 **5개 전문 에이전트**로 팀을 구성하여 병렬 개발을 진행한다.

```
                    ┌─────────────────────┐
                    │    Team Lead        │
                    │  (Orchestrator)     │
                    │  태스크 분배/리뷰    │
                    └──────────┬──────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
    ┌─────▼─────┐      ┌──────▼──────┐     ┌──────▼──────┐
    │ Agent 1    │      │ Agent 2      │     │ Agent 3     │
    │ Exchange   │      │ Detection    │     │ Backtest    │
    │ Infra      │      │ & Execution  │     │ & Analytics │
    └─────┬─────┘      └──────┬──────┘     └──────┬──────┘
          │                    │                    │
    ┌─────▼─────┐      ┌──────▼──────┐            │
    │ Agent 4    │      │ Agent 5      │            │
    │ Dashboard  │      │ DevOps &     │            │
    │ & Alerts   │      │ Testing      │            │
    └───────────┘      └─────────────┘            │
```

---

## 2. 에이전트 상세 정의

### Agent 1: Exchange Infrastructure (거래소 인프라)

**역할**: 거래소 연결, 데이터 수집, 저장 파이프라인

**담당 태스크**:
- T-010 ~ T-017: 거래소 커넥터 (Base, Binance, OKX, Bybit, Upbit, Bithumb)
- T-020 ~ T-022: 확장 커넥터 (KuCoin, Gate.io, Bitget)
- T-023 ~ T-026: 데이터 정규화, 캐싱, 저장소
- T-030 ~ T-035: 가격 수집 오케스트레이터, 레이턴시 측정
- T-090 ~ T-092: 잔고 모니터링, 리밸런싱

**필요 스킬**:
- Python asyncio/WebSocket 전문
- ccxt 라이브러리 숙련
- Redis, ClickHouse 데이터 파이프라인
- 거래소 API 문서 이해

**주요 파일**:
```
src/arbot/connectors/*
src/arbot/storage/*
src/arbot/rebalancer/*
```

**병렬 작업 가능 범위**:
- 각 거래소 커넥터는 독립적으로 개발 가능 (T-013 ~ T-022)
- 데이터 저장 레이어는 커넥터와 병렬 개발 가능 (T-024 ~ T-026)

---

### Agent 2: Detection & Execution (탐지 & 실행)

**역할**: 차익거래 기회 탐지, 주문 실행 엔진

**담당 태스크**:
- T-040 ~ T-045: 스프레드 계산, Spatial/Triangular 탐지
- T-060 ~ T-064: 페이퍼 트레이딩 실행 엔진
- T-080 ~ T-084: 통계적 차익거래 (Phase 2)
- T-110 ~ T-115: 실전 주문 실행 (Phase 3)
- T-120 ~ T-124: 자동 리밸런싱, 장애 복구 (Phase 3)

**필요 스킬**:
- 금융 알고리즘/퀀트 트레이딩
- 통계학 (공적분, Z-Score, 시계열 분석)
- Python asyncio (동시 주문 실행)
- 오더북 매칭 로직 이해

**주요 파일**:
```
src/arbot/detector/*
src/arbot/execution/*
```

**의존성**:
- Agent 1의 커넥터 인터페이스 (T-010) 완료 필요
- Redis 캐시 (T-024) 완료 필요

---

### Agent 3: Backtesting & Analytics (백테스팅 & 분석)

**역할**: 백테스팅 프레임워크, 성과 분석, 전략 최적화

**담당 태스크**:
- T-050 ~ T-055: 백테스팅 엔진, 시뮬레이터, 성과 지표
- T-070 ~ T-075: 리스크 관리 모듈
- T-102 ~ T-104: 전략 파라미터 최적화, 비교 분석

**필요 스킬**:
- 퀀트 리서치/백테스팅 방법론
- pandas/numpy/statsmodels 데이터 분석
- 리스크 관리 지표 (Sharpe, VaR, Drawdown)
- 통계적 검증 (Walk-Forward, 과적합 방지)

**주요 파일**:
```
src/arbot/backtest/*
src/arbot/risk/*
```

**의존성**:
- ClickHouse 히스토리컬 데이터 (T-025, T-034) 필요
- 탐지 모듈 인터페이스 (T-041) 필요

---

### Agent 4: Dashboard & Alerts (대시보드 & 알림)

**역할**: 모니터링 대시보드, 알림 시스템, 시각화

**담당 태스크**:
- T-094 ~ T-095: Prometheus 메트릭, Grafana 대시보드
- T-100 ~ T-101: Telegram 알림, 알림 매니저
- T-130 ~ T-134: 웹 대시보드 (Next.js)

**필요 스킬**:
- TypeScript/React/Next.js
- Grafana/Prometheus 모니터링
- WebSocket 기반 실시간 UI
- Telegram Bot API

**주요 파일**:
```
src/arbot/dashboard/*
src/arbot/alerts/*
dashboard/*  (Next.js 프론트엔드)
```

**병렬 작업**:
- Telegram 알림 (T-100)은 독립 개발 가능
- Grafana 대시보드 (T-095)는 독립 개발 가능
- 웹 대시보드 (T-130+)는 API 인터페이스 정의 후 독립 개발 가능

---

### Agent 5: DevOps & Testing (인프라 & 테스트)

**역할**: 프로젝트 초기 설정, CI/CD, 테스트, 보안

**담당 태스크**:
- T-001 ~ T-006: 프로젝트 스캐폴딩, Docker, DB 초기화
- T-140 ~ T-145: 성능 최적화, 보안 강화
- 전체 프로젝트 테스트 코드 작성/관리

**필요 스킬**:
- Python 프로젝트 구성 (pyproject.toml, ruff, mypy)
- Docker/Docker Compose
- PostgreSQL/ClickHouse/Redis 운영
- pytest, 테스트 설계
- 보안 (API 키 암호화, 감사 로그)

**주요 파일**:
```
pyproject.toml
docker/
configs/
tests/
scripts/
```

**우선 실행**:
- T-001 ~ T-006은 모든 에이전트의 전제조건 → 최우선 완료

---

## 3. 스프린트별 에이전트 배정

### Sprint 1 (Week 1-2): 프로젝트 부트스트랩

| 에이전트 | 태스크 | 비고 |
|----------|--------|------|
| Agent 5 (DevOps) | T-001, T-002, T-003, T-004, T-006 | **선행 작업** - 다른 에이전트 블로킹 해제 |
| Agent 2 (Detection) | T-005 | 데이터 모델 정의 |
| Agent 4 (Dashboard) | T-100 | Telegram 알림 봇 (독립 개발 가능) |

### Sprint 2 (Week 3-4): 거래소 커넥터 Core

| 에이전트 | 태스크 | 비고 |
|----------|--------|------|
| Agent 1 (Exchange) | T-010, T-011, T-012, T-013, T-014 | 핵심 커넥터 |
| Agent 2 (Detection) | T-040, T-043 | 스프레드 계산기, Signal 모델 |
| Agent 5 (DevOps) | 커넥터 테스트 코드 | T-013, T-014 테스트 |

### Sprint 3 (Week 5-6): 커넥터 확장 & 저장소

| 에이전트 | 태스크 | 비고 |
|----------|--------|------|
| Agent 1 (Exchange) | T-015, T-016, T-023, T-024, T-025, T-026 | 추가 커넥터 + 저장소 |
| Agent 2 (Detection) | T-041, T-042, T-044 | 탐지 엔진 |
| Agent 3 (Backtest) | T-034 | 히스토리컬 데이터 수집기 |
| Agent 4 (Dashboard) | T-101 | 알림 매니저 |

### Sprint 4 (Week 7-8): 수집 엔진 통합

| 에이전트 | 태스크 | 비고 |
|----------|--------|------|
| Agent 1 (Exchange) | T-030, T-031, T-032, T-033 | 오케스트레이터 |
| Agent 2 (Detection) | T-045 | 탐지 벤치마크 |
| Agent 3 (Backtest) | T-050, T-051 | 데이터 로더, 시뮬레이터 |
| Agent 5 (DevOps) | T-035 | E2E 테스트 |

### Sprint 5-6 (Week 9-12): 백테스팅 & 페이퍼 트레이딩

| 에이전트 | 태스크 | 비고 |
|----------|--------|------|
| Agent 1 (Exchange) | T-017, T-020, T-021, T-022 | 추가 거래소 |
| Agent 2 (Detection) | T-060, T-061, T-062, T-063 | 페이퍼 트레이딩 |
| Agent 3 (Backtest) | T-052, T-053, T-054, T-055 | 백테스팅 엔진 |
| Agent 4 (Dashboard) | T-064, T-094, T-095 | API + Grafana |

### Sprint 7-8 (Week 13-18): 리스크 & 통계 전략

| 에이전트 | 태스크 | 비고 |
|----------|--------|------|
| Agent 2 (Detection) | T-080, T-081, T-082, T-083 | 통계적 차익 |
| Agent 3 (Backtest) | T-070~T-075, T-084 | 리스크 관리 + Stat Arb 검증 |
| Agent 1 (Exchange) | T-090~T-093 | 리밸런싱 |
| Agent 4 (Dashboard) | T-102~T-104 | 전략 비교 리포트 |

### Sprint 9-11 (Week 19-30): 실전 & 대시보드

| 에이전트 | 태스크 | 비고 |
|----------|--------|------|
| Agent 2 (Detection) | T-110~T-115, T-120~T-124 | 실전 실행 |
| Agent 4 (Dashboard) | T-130~T-134 | 웹 대시보드 |
| Agent 5 (DevOps) | T-140~T-145 | 최적화 + 보안 |
| Agent 1 (Exchange) | 프로덕션 인프라 | 클라우드 배포 |
| Agent 3 (Backtest) | 지속 전략 검증 | 실전 vs 백테스트 비교 |

---

## 4. 에이전트 간 인터페이스 계약

### Interface 1: Exchange Connector → Detection Engine
```python
# Agent 1이 제공, Agent 2가 소비
class PriceUpdate:
    exchange: str
    symbol: str
    timestamp: float
    best_bid: float
    best_ask: float
    orderbook: OrderBook  # depth levels

# Redis Pub/Sub 채널: "price:{exchange}:{symbol}"
```

### Interface 2: Detection Engine → Execution Engine
```python
# Agent 2 내부
class ArbitrageSignal:
    id: UUID
    strategy: str          # SPATIAL | TRIANGULAR | STATISTICAL
    buy_exchange: str
    sell_exchange: str
    symbol: str
    buy_price: float
    sell_price: float
    quantity: float
    net_spread_pct: float
    confidence: float

# Redis Stream: "signals"
```

### Interface 3: Execution Engine → Risk Manager
```python
# Agent 2 → Agent 3
class TradeRequest:
    signal: ArbitrageSignal
    max_position_usd: float

class RiskDecision:
    approved: bool
    reason: str
    adjusted_quantity: float | None
```

### Interface 4: All Agents → Dashboard
```python
# 모든 에이전트 → Agent 4
# Prometheus 메트릭 (자동 수집)
# PostgreSQL 기록 (쿼리)
# Redis 실시간 데이터 (구독)
```

---

## 5. 에이전트 팀 운영 규칙

### 5.1 코드 컨벤션
- Python: ruff 포맷터 + mypy strict 타입 체크
- 타입 힌트 필수 (Python 3.12+ 문법)
- Docstring: Google 스타일
- 커밋 메시지: Conventional Commits (feat:, fix:, refactor:)

### 5.2 브랜치 전략
```
main ─── develop ─── feature/{agent}-{task-id}
                  ─── feature/exchange-binance-connector
                  ─── feature/detection-spatial
                  ─── feature/backtest-engine
```

### 5.3 코드 리뷰
- 각 에이전트 PR은 Team Lead 또는 관련 에이전트가 리뷰
- 인터페이스 변경은 영향받는 모든 에이전트 승인 필요

### 5.4 커뮤니케이션
- 인터페이스 변경: 즉시 브로드캐스트
- 블로커 발생: Team Lead에 에스컬레이션
- 일일 동기화: 각 에이전트 진행 상황 공유

---

## 6. 리스크 & 완화 전략

| 리스크 | 영향 | 완화 |
|--------|------|------|
| Agent 1 지연 → 전체 블로킹 | 높음 | Sprint 1에서 최소 2개 커넥터(Binance, OKX) 우선 완료 |
| 인터페이스 불일치 | 중간 | Sprint 1에서 Pydantic 모델 확정 후 공유 |
| 거래소 API 변경 | 중간 | ccxt 추상화 레이어 활용, 변경 감지 자동화 |
| 백테스트 과적합 | 높음 | Walk-Forward 분석 필수, Out-of-Sample 검증 |
| Phase 3 전환 리스크 | 높음 | 소규모 자금 테스트, 점진적 확대 |

---

## 7. Claude Code 에이전트 팀 실행 계획

실제 Claude Code에서 에이전트 팀을 구성할 때의 실행 방법:

### 7.1 팀 생성
```
TeamCreate: team_name="arbot", description="ArBot 차익거래 시스템 개발"
```

### 7.2 에이전트 스폰 (병렬)
```
Agent 1: subagent_type="general-purpose", name="exchange-infra"
Agent 2: subagent_type="general-purpose", name="detection-execution"
Agent 3: subagent_type="general-purpose", name="backtest-analytics"
Agent 4: subagent_type="general-purpose", name="dashboard-alerts"
Agent 5: subagent_type="general-purpose", name="devops-testing"
```

### 7.3 실행 순서
1. **Agent 5 (DevOps)** 먼저 실행 → T-001~T-006 완료
2. **Agent 1 (Exchange)** + **Agent 2 (Detection)** 병렬 실행
3. **Agent 3 (Backtest)** 데이터 레이어 준비 후 실행
4. **Agent 4 (Dashboard)** 독립 모듈부터 선 개발

### 7.4 Phase별 에이전트 활동량

```
             Week 1-8    Week 9-14   Week 15-22  Week 23-30
Agent 1:     ████████    ████        ████        ██
Agent 2:     ██████      ████████    ████████    ████████████
Agent 3:     ██          ████████    ████████████ ████
Agent 4:     ████        ████        ████        ████████████
Agent 5:     ████████    ██          ██          ████████
```

---

## 8. 성공 기준 (Definition of Done)

### Phase 1 완료 기준
- [ ] 5+ 거래소 실시간 가격 수집 안정 동작 (24시간+)
- [ ] Spatial Arbitrage 기회 실시간 탐지
- [ ] 백테스팅 1개월치 데이터 실행 완료
- [ ] 페이퍼 트레이딩 7일 연속 동작
- [ ] 단위 테스트 커버리지 80%+

### Phase 2 완료 기준
- [ ] 3가지 전략 (Spatial, Triangular, Statistical) 백테스팅 완료
- [ ] 리스크 관리 모듈 통합
- [ ] 페이퍼 트레이딩 30일 양의 수익률
- [ ] Grafana 대시보드 운영
- [ ] 전략 파라미터 최적화 1회 이상 실행

### Phase 3 전환 기준 (Gate Review)
- [ ] 페이퍼 트레이딩 60일+ 누적 양의 수익
- [ ] Max Drawdown < 2%
- [ ] Sharpe Ratio > 1.5
- [ ] Win Rate > 55%
- [ ] 모든 리스크 관리 모듈 통합 테스트 통과
- [ ] 보안 점검 완료
- [ ] 긴급 정지 시스템 테스트 완료
