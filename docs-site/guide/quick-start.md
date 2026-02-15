# 빠른 시작

5분 만에 ArBot 페이퍼 트레이딩을 시작하는 방법을 안내합니다.

## Step 1: 저장소 클론

```bash
git clone https://github.com/geniuskey/arbot.git
cd arbot
```

## Step 2: 의존성 설치

```bash
# 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 패키지 설치 (개발 의존성 포함)
pip install -e ".[dev]"
```

## Step 3: Docker 인프라 시작

```bash
# PostgreSQL, ClickHouse, Redis 실행
docker compose -f docker/docker-compose.yml up -d
```

서비스가 정상 실행되었는지 확인합니다:

```bash
docker compose -f docker/docker-compose.yml ps
```

## Step 4: 설정 파일 확인

### 환경 변수 설정

```bash
# .env 파일 생성
cp .env.example .env
```

`.env` 파일에 필요한 API 키와 데이터베이스 비밀번호를 설정합니다:

```bash
# 최소 설정 (페이퍼 트레이딩)
POSTGRES_PASSWORD=your-password
REDIS_PASSWORD=your-password
```

### 기본 설정 확인

`configs/default.yaml`에서 기본 설정을 확인합니다:

```yaml
system:
  execution_mode: paper    # 페이퍼 트레이딩 모드 (기본값)

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
  - SOL/USDT
  - XRP/USDT
  - DOGE/USDT
```

::: tip
페이퍼 트레이딩 모드에서는 실제 거래소 API 키 없이도 가격 데이터를 수집하고 가상 매매를 시뮬레이션할 수 있습니다.
:::

## Step 5: 페이퍼 트레이딩 실행

```bash
# ArBot 시작 (페이퍼 트레이딩 모드)
arbot
```

또는 모듈로 직접 실행:

```bash
python -m arbot
```

실행되면 다음과 같은 동작이 수행됩니다:

1. 설정된 거래소에 WebSocket 연결
2. 실시간 오더북 데이터 수집 시작
3. 차익거래 기회 탐지 (Spatial, Triangular)
4. 기회 발견 시 가상 매매 실행
5. PnL 실시간 추적 및 기록

### 로그 확인

ArBot은 `structlog` 기반 구조화 로깅을 사용합니다. 로그에서 다음 정보를 확인할 수 있습니다:

- 거래소 연결 상태
- 탐지된 차익거래 기회
- 가상 매매 실행 결과
- PnL 요약

## 다음 단계

ArBot의 기본 실행을 확인했다면, 다음 문서를 참고하여 더 깊이 알아보세요:

- **[설정](/guide/configuration)** - `default.yaml`과 `exchanges.yaml`의 상세 설정 방법
- **[아키텍처](/concepts/architecture)** - 시스템 구조와 데이터 흐름 이해
- **[차익거래 전략](/concepts/strategies)** - 4가지 전략의 원리와 파라미터
- **[페이퍼 트레이딩](/usage/paper-trading)** - 페이퍼 트레이딩 상세 활용법
- **[백테스팅](/usage/backtesting)** - 히스토리컬 데이터 기반 전략 검증
