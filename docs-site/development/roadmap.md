# 개발 로드맵

ArBot의 개발은 4개 Phase, 15개 Sprint로 구성됩니다. 백테스팅/시뮬레이션에서 시작하여 점진적으로 실전 트레이딩으로 전환하는 안전한 경로를 따릅니다.

## 전체 타임라인

| Phase | 기간 | 핵심 목표 |
|-------|------|-----------|
| **Phase 1** | Week 1-8 (Sprint 1-4) | 기반 인프라 & 데이터 수집 |
| **Phase 1.5** | Week 9-14 (Sprint 5-7) | 차익거래 탐지 & 시뮬레이션 |
| **Phase 2** | Week 15-22 (Sprint 8-11) | 리스크 관리 & 알고리즘 최적화 |
| **Phase 3** | Week 23-30 (Sprint 12-15) | 실전 트레이딩 |

## Phase 1: 기반 인프라 & 데이터 수집

### Sprint 1: 프로젝트 초기 설정 (Week 1-2)

- Python 프로젝트 구조 생성, `pyproject.toml`, ruff/mypy 설정
- Docker Compose 환경 구성 (PostgreSQL, ClickHouse, Redis, Grafana)
- YAML 기반 설정 관리 시스템 (Pydantic Settings)
- structlog 기반 구조화 로깅
- Pydantic 데이터 모델 정의 (OrderBook, Trade, Signal, Balance)
- DB 스키마 초기화 및 마이그레이션

### Sprint 2: 거래소 커넥터 - Core (Week 3-4)

- BaseConnector 추상 인터페이스 구현
- WebSocket Manager (연결 풀, 자동 재연결, 하트비트)
- 거래소별 Rate Limiter 구현
- **Binance** 커넥터 (P0)
- **OKX** 커넥터 (P0)
- **Bybit** 커넥터 (P1)
- **Upbit** 커넥터 (P1) - KRW 마켓

### Sprint 3: 거래소 커넥터 - Extended & 데이터 저장 (Week 5-6)

- KuCoin, Gate.io, Bitget 커넥터 (P2)
- 데이터 정규화 레이어 (거래소별 포맷 통합)
- Redis 가격 캐시 (실시간 가격 인메모리 캐싱, Pub/Sub)
- ClickHouse 적재 파이프라인 (틱 데이터 배치 적재)
- PostgreSQL CRUD (거래 기록, 시그널, 포트폴리오)

### Sprint 4: 가격 수집 엔진 통합 (Week 7-8)

- 다중 거래소 동시 연결 오케스트레이터
- 타임스탬프 동기화 (NTP 보정)
- 레이턴시 측정기
- 연결 상태 모니터링 (헬스체크, 자동 복구)
- 히스토리컬 데이터 수집기 (ccxt)
- E2E 통합 테스트

::: info 마일스톤 M1: 데이터 수집 파이프라인
**성공 기준**: 5개 이상 거래소의 실시간 가격 수집, 틱 데이터 ClickHouse 저장
:::

## Phase 1.5: 차익거래 탐지 & 시뮬레이션

### Sprint 5: 차익거래 탐지 엔진 (Week 9-10)

- 수수료/슬리피지 반영 스프레드 계산기
- **Spatial Detector** - 거래소 간 가격차 실시간 탐지
- **Triangular Detector** - 단일 거래소 삼각 차익 탐지
- ArbitrageSignal 데이터 모델, 큐잉, 로깅
- Redis Streams 기반 이벤트 스트림
- 탐지 성능 벤치마크 (목표: <10ms)

### Sprint 6: 백테스팅 프레임워크 (Week 11-12)

- ClickHouse 히스토리컬 데이터 로더
- 오더북 기반 시장 시뮬레이터 (슬리피지, 부분 체결 모델링)
- 이벤트 드리븐 백테스팅 엔진
- 성과 지표 계산기 (PnL, Sharpe Ratio, Max Drawdown, Win Rate, Profit Factor)
- HTML/JSON 백테스트 리포트
- Walk-Forward 분석 (P2)

### Sprint 7: 페이퍼 트레이딩 (Week 13-14)

- Paper Executor (실시간 가격 기반 가상 체결)
- 가상 포트폴리오 관리 (잔고, 포지션 추적)
- 실시간 PnL 트래커
- 탐지 -> 리스크체크 -> 가상실행 전체 파이프라인 통합
- REST API로 시뮬레이션 결과 제공

::: info 마일스톤 M2: 탐지 & 시뮬레이션
**성공 기준**: 차익 기회 실시간 탐지, 백테스팅 프레임워크 완성, 페이퍼 트레이딩 안정 운영
:::

## Phase 2: 리스크 관리 & 알고리즘 최적화

### Sprint 8: 리스크 관리 모듈 (Week 15-16)

- Risk Manager 코어 (시그널 필터링 파이프라인)
- 포지션 리미터 (코인별/거래소별/전체 한도)
- Drawdown 모니터 (실시간 드로다운, 임계값 경고/정지)
- 이상 가격 감지기 (Flash Crash, 비정상 스프레드)
- Circuit Breaker (연속 손실 시 자동 중단)
- 백테스팅 기반 리스크 파라미터 최적 탐색

### Sprint 9: 통계적 차익거래 (Week 17-18)

- 공적분 분석기 (Engle-Granger / Johansen 테스트)
- 자동 공적분 페어 스캐너
- 실시간 Z-Score 계산 및 진입/청산 시그널
- **Statistical Detector** 엔진 통합
- 통계적 차익 전략 백테스팅

### Sprint 10: 리밸런싱 & 모니터링 (Week 19-20)

- 다중 거래소 잔고 실시간 모니터링
- 최적 자금 이동 경로/금액 계산
- 전송 네트워크 자동 선택 (수수료/속도 최적화)
- 리밸런싱 알림 (자동 실행은 Phase 3)
- Prometheus 메트릭 수집
- Grafana 사전 구성 대시보드

### Sprint 11: 알림 & 알고리즘 최적화 (Week 21-22)

- Telegram 알림 봇 (기회 탐지, PnL, 에러, 서킷 브레이커)
- 알림 매니저 (우선순위, 스로틀링, 중복 방지)
- 그리드 서치/베이지안 최적화 파라미터 탐색
- 다중 전략 비교 리포트 (Spatial vs Triangular vs Statistical)
- 실시간 vs 백테스트 괴리 분석

::: info 마일스톤 M3: 리스크 & 최적화
**성공 기준**: 리스크 관리 시스템 가동, 통계적 차익거래 추가, 모니터링/알림 시스템 완성, 전략 최적화 완료
:::

## Phase 3: 실전 트레이딩

### Sprint 12: 실전 실행 엔진 (Week 23-24)

- Live Executor (실제 거래소 API 주문 - Limit, IOC)
- 양 거래소 동시 매수/매도 (`asyncio.gather`)
- 주문 상태 실시간 추적 (체결/부분체결/취소)
- 부분 체결 잔여 포지션 처리
- 탐지 -> 리스크 -> 실행 -> 기록 전체 파이프라인
- 긴급 정지 시스템 (원클릭 포지션 정리 + 봇 중단)

### Sprint 13: 자동 리밸런싱 & 안정화 (Week 25-26)

- 임계값 초과 시 자동 자금 이동
- 출금/입금 블록체인 확인 상태 추적
- 그레이스풀 셧다운 (미체결 주문 취소, 포지션 정리)
- 비정상 종료 후 상태 복구 (Resume)
- 고변동성/고지연 스트레스 테스트

### Sprint 14: 대시보드 & 운영 도구 (Week 27-28)

- Next.js 웹 대시보드
- 거래소별 실시간 가격 비교 차트
- 거래 히스토리 뷰어 (검색/필터/상세)
- 일별/주별/월별 PnL 대시보드
- 시스템 상태 패널 (연결, 레이턴시, 에러율)

### Sprint 15: 성능 최적화 & 보안 강화 (Week 29-30)

- uvloop 적용 (이벤트 루프 성능 2~4x 향상)
- orjson 적용 (JSON 파싱 10x 향상)
- cProfile/py-spy 핫패스 프로파일링
- API 키 AES-256 암호화 저장/로딩
- 감사 로그 (모든 주문/리밸런싱 활동 불변 기록)
- API 키 권한 분리, IP 화이트리스트 검증

::: info 마일스톤 M4: 실전 트레이딩
**성공 기준**: 실전 주문 실행, 자동 리밸런싱, 웹 대시보드, 보안 강화 완료
:::

## 의존성 그래프

```
T-001 ──> T-003 ──> T-010 ──> T-013 ──> T-030 ──> T-041 ──> T-063 ──> T-114
  │         │                    │                    │
  │         │                    v                    v
  │         │              T-014, T-015         T-042, T-083
  │         │
  v         v
T-004    T-005 ──> T-043

T-002 ──> T-006 ──> T-025 ──> T-050 ──> T-052 ──> T-053
  │                    │
  v                    v
T-024              T-034
```

핵심 경로: 프로젝트 설정 -> 설정 관리 -> 커넥터 -> 가격 수집 -> 기회 탐지 -> 페이퍼 트레이딩 -> 실전 트레이딩
