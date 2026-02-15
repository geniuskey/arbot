# Docker 배포

ArBot은 Docker Compose를 사용하여 모든 인프라 서비스를 쉽게 구성할 수 있습니다.

## Docker Compose 구성

`docker/docker-compose.yml` 파일에 다음 서비스가 정의되어 있습니다.

### 서비스 목록

| 서비스 | 이미지 | 포트 | 역할 |
|--------|--------|------|------|
| **postgres** | `postgres:16` | 5432 | 거래 기록, 설정, 메타데이터 저장 |
| **clickhouse** | `clickhouse/clickhouse-server:24` | 9000, 8123 | 틱 데이터, 오더북 스냅샷 (시계열) |
| **redis** | `redis:7-alpine` | 6379 | 실시간 가격 캐시, Pub/Sub 메시지 브로커 |
| **grafana** | `grafana/grafana:11` | 3000 | 모니터링 대시보드 시각화 |
| **prometheus** | `prom/prometheus:v2` | 9090 | 시스템 메트릭 수집 |
| **arbot** | 자체 빌드 | - | ArBot 메인 애플리케이션 |

### docker-compose.yml

```yaml
services:
  postgres:
    image: postgres:16
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: arbot
      POSTGRES_USER: arbot
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-arbot_dev}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U arbot"]
      interval: 10s
      timeout: 5s
      retries: 5

  clickhouse:
    image: clickhouse/clickhouse-server:24
    ports:
      - "9000:9000"
      - "8123:8123"
    volumes:
      - chdata:/var/lib/clickhouse

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    command: redis-server --requirepass ${REDIS_PASSWORD:-redis_dev}
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD:-redis_dev}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  grafana:
    image: grafana/grafana:11
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin}
    volumes:
      - grafanadata:/var/lib/grafana
    depends_on:
      - prometheus

  prometheus:
    image: prom/prometheus:v2
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - promdata:/prometheus

  arbot:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
      clickhouse:
        condition: service_started
      redis:
        condition: service_healthy
    env_file:
      - ../.env
    volumes:
      - ../configs:/app/configs

volumes:
  pgdata:
  chdata:
  grafanadata:
  promdata:
```

## 서비스별 상세 설명

### PostgreSQL

- **포트**: 5432
- **볼륨**: `pgdata` - 데이터 영속 저장
- **헬스체크**: `pg_isready` 명령으로 10초 간격 확인, 5회 재시도
- **환경변수**: `POSTGRES_PASSWORD`로 비밀번호 설정 (기본값: `arbot_dev`)
- **용도**: 거래 기록, 차익거래 시그널, 포트폴리오 스냅샷, 일일 성과 데이터

### ClickHouse

- **포트**: 9000 (Native), 8123 (HTTP)
- **볼륨**: `chdata` - 시계열 데이터 영속 저장
- **용도**: 오더북 스냅샷, 체결(틱) 데이터, 스프레드 히스토리
- **TTL**: 오더북 90일, 틱 180일, 스프레드 365일 자동 삭제

### Redis

- **포트**: 6379
- **헬스체크**: `redis-cli ping` 명령으로 10초 간격 확인
- **환경변수**: `REDIS_PASSWORD`로 비밀번호 설정 (기본값: `redis_dev`)
- **용도**: 실시간 가격 캐시, Redis Streams 기반 이벤트 전달

### Grafana

- **포트**: 3000
- **볼륨**: `grafanadata` - 대시보드 설정 영속 저장
- **의존성**: Prometheus 서비스 시작 후 기동
- **기본 로그인**: admin / `GRAFANA_PASSWORD` (기본값: `admin`)

### Prometheus

- **포트**: 9090
- **볼륨**: `promdata` - 메트릭 데이터 영속 저장
- **설정**: `docker/prometheus.yml` 파일로 스크레이프 대상 설정

### ArBot

- **빌드**: 프로젝트 루트의 `docker/Dockerfile`로 빌드
- **의존성**: PostgreSQL(healthy), ClickHouse(started), Redis(healthy) 확인 후 시작
- **환경변수**: 프로젝트 루트 `.env` 파일에서 로드
- **볼륨**: `configs/` 디렉토리를 컨테이너 내부로 마운트

## 시작 / 중지

### 전체 시작

```bash
cd docker
docker compose up -d
```

### 전체 중지

```bash
docker compose down
```

### 중지 + 볼륨 삭제 (데이터 초기화)

```bash
docker compose down -v
```

::: warning 주의
`-v` 옵션은 모든 데이터 볼륨을 삭제합니다. 운영 데이터가 있는 경우 반드시 백업 후 실행하세요.
:::

## 개별 서비스 관리

### 특정 서비스만 시작

```bash
docker compose up -d postgres redis
```

### 특정 서비스 재시작

```bash
docker compose restart arbot
```

### ArBot만 다시 빌드

```bash
docker compose build arbot
docker compose up -d arbot
```

### 서비스 상태 확인

```bash
docker compose ps
```

## 로그 확인

### 전체 로그

```bash
docker compose logs -f
```

### 특정 서비스 로그

```bash
docker compose logs -f arbot
docker compose logs -f postgres
```

### 최근 100줄만 확인

```bash
docker compose logs --tail 100 arbot
```

## 데이터 영속성

Docker Compose에서 Named Volume을 사용하여 컨테이너가 재시작되어도 데이터가 유지됩니다.

| 볼륨 | 서비스 | 경로 | 설명 |
|------|--------|------|------|
| `pgdata` | PostgreSQL | `/var/lib/postgresql/data` | 거래 기록, 설정 데이터 |
| `chdata` | ClickHouse | `/var/lib/clickhouse` | 틱 데이터, 오더북 스냅샷 |
| `grafanadata` | Grafana | `/var/lib/grafana` | 대시보드 설정 |
| `promdata` | Prometheus | `/prometheus` | 메트릭 히스토리 |

### 볼륨 백업

```bash
# PostgreSQL 백업
docker compose exec postgres pg_dump -U arbot arbot > backup.sql

# 볼륨 위치 확인
docker volume inspect docker_pgdata
```

## Dockerfile 개요

ArBot 애플리케이션은 `docker/Dockerfile`을 통해 컨테이너 이미지로 빌드됩니다.

```dockerfile
# 빌드 컨텍스트: 프로젝트 루트
# 주요 단계:
# 1. Python 3.12 베이스 이미지
# 2. 의존성 설치 (pyproject.toml)
# 3. 소스 코드 복사
# 4. 엔트리포인트 설정 (arbot 명령)
```

빌드 컨텍스트는 프로젝트 루트(`..`)이며, `configs/` 디렉토리는 볼륨으로 마운트하여 설정 변경 시 재빌드 없이 반영할 수 있습니다.
