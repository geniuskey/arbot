# Oracle Cloud 무료 배포

Oracle Cloud Always Free 티어를 활용하여 ArBot을 무료로 운영하는 방법을 안내합니다.

## Always Free 리소스

| 리소스 | 제공량 | ArBot 사용 |
|--------|--------|-----------|
| **VM (ARM)** | Ampere A1: 4 OCPU, 24GB RAM | ArBot + DB 전체 구동 |
| **Boot Volume** | 200GB | OS + Docker 이미지 |
| **네트워크** | 월 10TB 아웃바운드 | WebSocket 트래픽 |
| **기간** | 영구 무료 | - |

## 사전 준비

1. [Oracle Cloud 계정 생성](https://cloud.oracle.com/free) (신용카드 필요, 과금 없음)
2. SSH 키 쌍 준비

## Step 1: VM 인스턴스 생성

OCI 콘솔에서 Compute Instance를 생성합니다.

### 설정값

| 항목 | 값 |
|------|-----|
| **이름** | arbot |
| **이미지** | Ubuntu 22.04 (aarch64) |
| **Shape** | VM.Standard.A1.Flex |
| **OCPU** | 4 |
| **메모리** | 24 GB |
| **Boot Volume** | 100 GB (기본) |
| **리전** | 서울, 춘천, 도쿄 중 선택 |

::: tip 리전 선택
- **춘천 (ap-chuncheon-1)**: 국내 거래소(Upbit, Bithumb) 레이턴시 최적
- **도쿄 (ap-tokyo-1)**: 글로벌 거래소(Binance, OKX) 레이턴시 양호
- **서울 (ap-seoul-1)**: 균형적, 인스턴스 확보 어려울 수 있음
:::

### 네트워크 설정

VCN > Subnet > Security List에서 Ingress Rule 추가:

| 포트 | 프로토콜 | 소스 CIDR | 용도 |
|------|----------|-----------|------|
| 22 | TCP | 본인 IP/32 | SSH |
| 3000 | TCP | 본인 IP/32 | Grafana |
| 8080 | TCP | 본인 IP/32 | Dashboard |

::: warning 보안
포트를 `0.0.0.0/0`으로 열지 마세요. **본인 IP만** 허용하세요.
:::

## Step 2: VM 초기 설정

SSH 접속 후 setup 스크립트를 실행합니다.

```bash
ssh -i ~/.ssh/id_rsa ubuntu@<VM_PUBLIC_IP>

# 소스 클론
git clone https://github.com/geniuskey/arbot.git ~/arbot
cd ~/arbot

# 초기 설정 (Docker, 방화벽, swap)
chmod +x deploy/oracle-cloud/setup.sh
./deploy/oracle-cloud/setup.sh

# Docker 그룹 적용을 위해 재접속
exit
ssh -i ~/.ssh/id_rsa ubuntu@<VM_PUBLIC_IP>
```

## Step 3: 환경 변수 설정

```bash
cd ~/arbot/deploy/oracle-cloud
cp .env.example .env
nano .env
```

최소 필수 설정:

```env
HOST_IP=<VM_PUBLIC_IP>
POSTGRES_PASSWORD=<strong_password>
REDIS_PASSWORD=<strong_password>
GRAFANA_PASSWORD=<strong_password>
```

::: tip 비밀번호 생성
```bash
openssl rand -base64 24
```
:::

## Step 4: 서비스 시작

```bash
cd ~/arbot
docker compose -f deploy/oracle-cloud/docker-compose.yml up -d
```

상태 확인:

```bash
docker compose -f deploy/oracle-cloud/docker-compose.yml ps
```

```
NAME        STATUS              PORTS
postgres    Up (healthy)        127.0.0.1:5432->5432/tcp
redis       Up (healthy)        127.0.0.1:6379->6379/tcp
prometheus  Up                  127.0.0.1:9090->9090/tcp
grafana     Up                  0.0.0.0:3000->3000/tcp
arbot       Up                  0.0.0.0:8080->8080/tcp
watchtower  Up
```

## Step 5: 확인

### 로그 확인

```bash
docker compose -f deploy/oracle-cloud/docker-compose.yml logs -f arbot
```

### Grafana 접속

브라우저에서 `http://<VM_PUBLIC_IP>:3000` 접속

- ID: `admin`
- PW: `.env`에서 설정한 `GRAFANA_PASSWORD`

## 메모리 배분

24GB RAM을 다음과 같이 배분합니다.

| 서비스 | 메모리 제한 | 비고 |
|--------|-----------|------|
| ArBot | 4 GB | 메인 봇 |
| PostgreSQL | 2 GB | 거래 기록 |
| Redis | 1 GB | 캐시, LRU 정책 |
| Prometheus | 512 MB | 30일 보관 |
| Grafana | 512 MB | 대시보드 |
| Watchtower | 128 MB | 자동 업데이트 |
| OS + 여유 | ~16 GB | 충분한 버퍼 |

## 운영 명령어

```bash
# 전체 시작
docker compose -f deploy/oracle-cloud/docker-compose.yml up -d

# 전체 중지
docker compose -f deploy/oracle-cloud/docker-compose.yml down

# ArBot만 재시작
docker compose -f deploy/oracle-cloud/docker-compose.yml restart arbot

# 로그 확인 (최근 100줄)
docker compose -f deploy/oracle-cloud/docker-compose.yml logs --tail 100 arbot

# 리소스 사용량
docker stats --no-stream

# 디스크 정리
docker system prune -f
```

## 자동 재시작

`docker-compose.yml`에 `restart: unless-stopped`가 설정되어 있어 VM 재부팅 시 자동 시작됩니다. Docker 서비스 자동 시작 확인:

```bash
sudo systemctl enable docker
```

## 업데이트

Watchtower가 매일 04:00에 Docker 이미지를 자동 업데이트합니다. 소스 코드 업데이트는 수동으로 진행합니다.

```bash
cd ~/arbot
git pull
docker compose -f deploy/oracle-cloud/docker-compose.yml up -d --build arbot
```

## Phase 3 전환 시

실전 트레이딩으로 전환할 때는 다음을 고려하세요.

- **레이턴시**: Oracle Cloud 리전과 거래소 서버 간 ping 측정
- **ClickHouse 추가**: 틱 데이터 양이 많아지면 별도 추가
- **백업**: PostgreSQL 일일 백업 크론잡 설정
- **모니터링 강화**: Grafana 알림 규칙 설정

```bash
# 거래소 레이턴시 측정
ping -c 10 api.binance.com
ping -c 10 www.okx.com
```
