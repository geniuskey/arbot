# ArBot - Crypto Cross-Exchange Arbitrage System

## 프로젝트 개요
암호화폐 크로스 거래소 차익거래 자동화 시스템. 전 세계 주요 거래소의 가격 차이를 실시간 감지하고 자동 매매로 차익 실현.

## 개발 단계
- **Phase 1**: 시뮬레이션 & 백테스팅 (MVP)
- **Phase 2**: 알고리즘 최적화 & 리스크 관리
- **Phase 3**: 실전 트레이딩

## 기술 스택
- **언어**: Python 3.12+ (코어), Rust (성능 크리티컬 - 향후)
- **DB**: PostgreSQL (거래기록), ClickHouse (틱데이터), Redis (캐시/Pub-Sub)
- **대시보드**: Next.js (TypeScript)
- **거래소 연동**: ccxt + 자체 WebSocket 커넥터

## 핵심 문서
- `docs/PRD.md` - 제품 요구사항 문서
- `docs/TRD.md` - 기술 요구사항 문서
- `docs/TASKS.md` - 개발 태스크 목록
- `docs/AGENT_TEAM_PLAN.md` - 에이전트 팀 구성 계획

## 코드 컨벤션
- Python: ruff 포매터, mypy strict 타입 체크
- 타입 힌트 필수 (Python 3.12+ 문법)
- Docstring: Google 스타일
- 커밋: Conventional Commits (feat:, fix:, refactor:)

## 프로젝트 구조
```
src/arbot/          # 메인 Python 패키지
  connectors/       # 거래소 커넥터 (WebSocket + REST)
  detector/         # 차익거래 기회 탐지
  execution/        # 주문 실행 (Paper + Live)
  risk/             # 리스크 관리
  backtest/         # 백테스팅 엔진
  rebalancer/       # 거래소 간 자금 리밸런싱
  storage/          # DB 저장소 (PG, CH, Redis)
  dashboard/        # 대시보드 API
  alerts/           # 알림 (Telegram)
dashboard/          # Next.js 프론트엔드
```
