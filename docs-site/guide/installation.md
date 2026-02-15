# 설치 가이드

## 시스템 요구사항

| 항목 | 최소 요구사항 |
|------|-------------|
| **Python** | 3.12 이상 |
| **Docker** | Docker Desktop 또는 Docker Engine + Docker Compose |
| **pnpm** | 9.x 이상 (대시보드 개발 시) |
| **OS** | Linux, macOS, Windows (WSL2 권장) |
| **메모리** | 4GB 이상 (Docker 인프라 포함 시 8GB 권장) |

## Python 패키지 설치

### 1. 저장소 클론

```bash
git clone https://github.com/geniuskey/arbot.git
cd arbot
```

### 2. 가상환경 생성

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### 3. 패키지 설치

일반 설치:

```bash
pip install -e .
```

개발 의존성 포함 설치:

```bash
pip install -e ".[dev]"
```

::: details 주요 의존성 목록

| 패키지 | 버전 | 용도 |
|--------|------|------|
| `ccxt` | >= 4.0 | 100+ 거래소 통합 라이브러리 |
| `websockets` | >= 12.0 | WebSocket 클라이언트 |
| `aiohttp` | >= 3.9 | 비동기 HTTP 클라이언트 |
| `numpy` | >= 1.26 | 수치 연산 |
| `pandas` | >= 2.2 | 시계열 데이터 처리 |
| `polars` | >= 0.20 | 고성능 DataFrame |
| `pydantic` | >= 2.6 | 데이터 모델 검증 |
| `redis` | >= 5.0 | 인메모리 캐시/Pub-Sub |
| `asyncpg` | >= 0.29 | PostgreSQL 비동기 드라이버 |
| `clickhouse-driver` | >= 0.2 | 시계열 데이터 저장 |
| `statsmodels` | >= 0.14 | 공적분 검정, 통계 모델링 |
| `scipy` | >= 1.12 | 과학 계산 |
| `cryptography` | >= 42.0 | API 키 암호화 |
| `structlog` | >= 24.0 | 구조화 로깅 |
| `orjson` | >= 3.9 | 고성능 JSON 파서 |

**개발 의존성** (`dev`):

| 패키지 | 버전 | 용도 |
|--------|------|------|
| `pytest` | >= 8.0 | 테스트 프레임워크 |
| `pytest-asyncio` | >= 0.23 | 비동기 테스트 |
| `pytest-cov` | - | 코드 커버리지 |
| `ruff` | >= 0.2 | 포매터/린터 |
| `mypy` | >= 1.8 | 정적 타입 체크 |

:::

## Docker 인프라 설치

ArBot은 PostgreSQL, ClickHouse, Redis를 인프라로 사용합니다. Docker Compose로 한 번에 실행할 수 있습니다.

### 기본 인프라 실행

```bash
docker compose -f docker/docker-compose.yml up -d
```

이 명령어로 다음 서비스가 실행됩니다:

| 서비스 | 포트 | 설명 |
|--------|------|------|
| **PostgreSQL 16** | 5432 | 거래 기록, 설정, 메타데이터 |
| **ClickHouse** | 9000, 8123 | 틱 데이터, 오더북 스냅샷 (시계열 데이터) |
| **Redis 7** | 6379 | 실시간 가격 캐시, Pub/Sub 메시지 브로커 |

### 모니터링 인프라 실행 (선택)

```bash
docker compose -f docker-compose.monitoring.yml up -d
```

| 서비스 | 포트 | 설명 |
|--------|------|------|
| **Grafana** | 3000 | 대시보드 시각화 |
| **Prometheus** | 9090 | 메트릭 수집 |

### 서비스 상태 확인

```bash
docker compose -f docker/docker-compose.yml ps
```

## 환경 변수 설정

### 1. `.env` 파일 생성

```bash
cp .env.example .env
```

### 2. 필수 값 설정

`.env` 파일을 열어 다음 항목을 설정합니다:

```bash
# 마스터 암호화 키 (API 키 암호화에 사용)
ARBOT_MASTER_KEY=your-secure-master-key

# 사용할 거래소의 API 키 설정
BINANCE_API_KEY=your-binance-api-key
BINANCE_API_SECRET=your-binance-api-secret

# 데이터베이스
POSTGRES_PASSWORD=your-db-password
REDIS_PASSWORD=your-redis-password

# 알림 (선택)
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-telegram-chat-id
```

::: warning 보안 주의사항
- `.env` 파일은 `.gitignore`에 포함되어 있어 Git에 커밋되지 않습니다
- 거래 전용 API 키를 사용하고 **출금 권한은 반드시 비활성화**하세요
- 가능하면 IP 화이트리스트를 설정하세요
- API 키는 AES-256으로 암호화되어 저장됩니다
:::

## 설치 확인

### Python 패키지 확인

```bash
# ArBot CLI 실행 확인
arbot --help

# 또는 모듈 직접 실행
python -m arbot --help
```

### Docker 인프라 확인

```bash
# PostgreSQL 연결 확인
docker exec -it arbot-postgres psql -U arbot -d arbot -c "SELECT 1"

# Redis 연결 확인
docker exec -it arbot-redis redis-cli ping

# ClickHouse 연결 확인
docker exec -it arbot-clickhouse clickhouse-client -q "SELECT 1"
```

### 테스트 실행

```bash
# 전체 테스트 실행
pytest

# 커버리지 포함
pytest --cov=arbot

# 타입 체크
mypy src/arbot

# 린트 체크
ruff check src/
```

모든 확인이 완료되면 [빠른 시작](/guide/quick-start) 가이드로 이동하세요.
