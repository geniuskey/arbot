# 프로덕션 배포

ArBot을 프로덕션 환경에 배포하기 위한 가이드입니다. 실전 트레이딩(Phase 3)에서 안정적으로 운영하기 위한 체크리스트와 설정을 다룹니다.

## 프로덕션 배포 체크리스트

배포 전 아래 항목을 모두 확인하세요.

### 보안

- [ ] 모든 API 키가 **거래 전용** (출금 권한 비활성화)
- [ ] `ARBOT_MASTER_KEY`로 API 키 AES-256 암호화 적용
- [ ] 모든 거래소에 IP 화이트리스트 설정
- [ ] `.env` 파일 권한 제한 (`chmod 600`)
- [ ] 프로덕션 DB 비밀번호가 강력한 랜덤 값으로 설정
- [ ] SSH 키 기반 인증 (패스워드 로그인 비활성화)

### 인프라

- [ ] Docker Compose 서비스 전체 정상 가동 확인
- [ ] PostgreSQL / ClickHouse / Redis 헬스체크 통과
- [ ] Prometheus + Grafana 모니터링 대시보드 설정
- [ ] Telegram 알림 봇 동작 확인

### 트레이딩

- [ ] 백테스팅 결과 양호 (양의 Sharpe Ratio)
- [ ] 페이퍼 트레이딩 2주 이상 안정 운영 완료
- [ ] 리스크 파라미터 설정 완료 (일일 손실 한도, 드로다운 한도)
- [ ] 서킷 브레이커 설정 확인
- [ ] 긴급 정지 시스템 테스트 완료

## AWS 배포 가이드

### 권장 리전

| 리전 | 위치 | 근접 거래소 | 용도 |
|------|------|-------------|------|
| `ap-northeast-1` | Tokyo | Binance, Bybit | 메인 서버 |
| `ap-southeast-1` | Singapore | OKX | 보조 서버 (Phase 3) |

거래소 매칭 엔진에 가까운 리전을 선택하여 네트워크 레이턴시를 최소화합니다.

### EC2 인스턴스

```
권장 사양:
- 인스턴스: c6i.xlarge (4 vCPU, 8GB RAM)
- 스토리지: gp3 100GB (IOPS 3000)
- 네트워크: Enhanced Networking 활성화
```

#### 배포 절차

```bash
# 1. 서버 접속
ssh -i arbot-key.pem ubuntu@<EC2-IP>

# 2. Docker 설치
sudo apt update && sudo apt install -y docker.io docker-compose-plugin

# 3. 프로젝트 클론
git clone https://github.com/geniuskey/arbot.git
cd arbot

# 4. 환경 변수 설정
cp .env.example .env
vim .env  # API 키, DB 비밀번호 등 입력

# 5. 서비스 시작
cd docker
docker compose up -d

# 6. 상태 확인
docker compose ps
docker compose logs -f arbot
```

### ECS (Elastic Container Service)

컨테이너 오케스트레이션이 필요한 경우 AWS ECS를 사용할 수 있습니다.

- **Fargate**: 서버리스 컨테이너 실행 (관리 부담 최소화)
- **EC2 launch type**: 직접 인스턴스 관리 (비용 최적화)
- AWS Secrets Manager로 API 키 관리
- CloudWatch 로그 통합

## Kubernetes 배포 (Phase 3)

Phase 3에서 이중화 및 자동 스케일링이 필요할 때 Kubernetes 도입을 검토합니다.

```
계획:
- EKS (Elastic Kubernetes Service) 또는 자체 관리 클러스터
- Helm Chart로 배포 자동화
- Primary + Failover 이중화 구성
- Pod 수준 헬스체크 및 자동 복구
```

::: info
Kubernetes 배포는 Phase 3에서 실전 트레이딩 안정화 이후 도입 예정입니다. Phase 1-2에서는 Docker Compose로 충분합니다.
:::

## 보안 설정

### API 키 암호화

```python
# AES-256 암호화 (cryptography 라이브러리)
# ARBOT_MASTER_KEY를 사용하여 모든 API 키를 암호화
# 런타임에만 메모리에서 복호화
```

- 마스터 키 길이: 256bit (64자 hex)
- 암호화 알고리즘: AES-256-GCM
- 키 저장: `.env` 파일 또는 AWS Secrets Manager

### IP 화이트리스트

모든 거래소 API에 서버 IP만 허용하도록 설정합니다.

```bash
# 서버 공인 IP 확인
curl ifconfig.me

# 각 거래소 API 설정에서 해당 IP만 허용
```

### 감사 로그

모든 주문, 리밸런싱 활동이 불변 로그로 기록됩니다.

- PostgreSQL `trades` 테이블에 모든 주문 기록
- 시그널 탐지/실행/거부 이력 추적 (`arbitrage_signals` 테이블)
- structlog 기반 구조화 로깅 (JSON 포맷)

## 성능 최적화

### uvloop

Python의 기본 이벤트 루프 대신 `uvloop`을 사용하면 2~4배 성능 향상을 얻을 수 있습니다.

```python
# Linux/macOS에서만 사용 가능 (Windows 미지원)
# pyproject.toml에 조건부 의존성으로 설정됨
"uvloop>=0.19; sys_platform != \"win32\""
```

### orjson

표준 `json` 모듈 대비 약 10배 빠른 JSON 파싱을 제공합니다.

```python
# WebSocket 메시지 파싱, API 응답 처리에 사용
# 자동으로 orjson이 설치되어 있으면 사용
```

### 추가 최적화

- `numpy` 벡터 연산으로 루프 최소화
- Redis 인메모리 캐시로 디스크 I/O 제거
- `asyncio` 기반 비동기 I/O로 동시 WebSocket 연결

## 모니터링 설정

### Prometheus 메트릭

ArBot은 `prometheus-client`를 통해 다음 메트릭을 노출합니다:

- **레이턴시**: 거래소별 WebSocket 수신 지연
- **연결 상태**: 거래소 연결 유지/끊김 횟수
- **PnL**: 실시간 손익
- **시그널**: 탐지/실행/거부된 차익 기회 수
- **시스템**: CPU, 메모리 사용량

### Grafana 대시보드

Grafana에서 사전 구성된 대시보드를 사용합니다.

- **가격 모니터링**: 거래소별 실시간 가격 비교
- **스프레드 차트**: 거래소 간 스프레드 추이
- **PnL 대시보드**: 일별/주별/월별 수익 추이
- **시스템 상태**: 서비스 가동률, 에러율, 레이턴시

### Telegram 알림

중요 이벤트를 실시간으로 Telegram으로 전송합니다.

- 차익 기회 탐지 (설정 임계값 이상)
- 일일 PnL 리포트
- 에러/장애 발생
- 서킷 브레이커 발동

## 백업 전략

### PostgreSQL

```bash
# 일일 자동 백업 (cron)
0 2 * * * docker compose exec -T postgres pg_dump -U arbot arbot | gzip > /backup/arbot_$(date +\%Y\%m\%d).sql.gz

# 복원
gunzip < backup.sql.gz | docker compose exec -T postgres psql -U arbot arbot
```

### ClickHouse

```bash
# 파티션 단위 백업
docker compose exec clickhouse clickhouse-client --query "BACKUP TABLE arbot.orderbook_snapshots TO '/backup/'"
```

### Redis

Redis는 캐시 용도이므로 백업이 필수는 아니지만, RDB 스냅샷을 설정할 수 있습니다.

```bash
# Redis RDB 스냅샷 (redis.conf에서 설정)
save 900 1
save 300 10
```

### 백업 보관 정책

| 대상 | 주기 | 보관 기간 |
|------|------|-----------|
| PostgreSQL | 매일 | 30일 |
| ClickHouse | 매주 | 90일 |
| 설정 파일 | 변경 시 | Git으로 버전 관리 |
