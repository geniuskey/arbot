# Oracle Cloud 무료 배포

Oracle Cloud Always Free 티어를 활용하여 ArBot을 무료로 운영하는 방법을 안내합니다.

## Always Free 리소스

| 리소스 | 제공량 | ArBot 사용 |
|--------|--------|-----------|
| **VM (ARM)** | Ampere A1: 4 OCPU, 24GB RAM | ArBot + DB 전체 구동 |
| **Boot Volume** | 200GB | OS + 데이터 |
| **네트워크** | 월 10TB 아웃바운드 | WebSocket 트래픽 |
| **기간** | 영구 무료 | - |

## 사전 준비

1. [Oracle Cloud 계정 생성](https://cloud.oracle.com/free) (신용카드 필요, 과금 없음)
2. SSH 키 쌍 준비 (아래 참고)

## SSH 키 생성

VM에 접속하려면 SSH 키 쌍(공개키 + 개인키)이 필요합니다.

### Windows (PowerShell)

```powershell
ssh-keygen -t ed25519 -C "arbot"
```

- 저장 경로: 기본값 `C:\Users\<사용자>\.ssh\id_ed25519` (Enter)
- 비밀번호: 설정 또는 빈칸 (Enter)

생성된 파일:

| 파일 | 용도 |
|------|------|
| `~/.ssh/id_ed25519` | 개인키 (절대 공유 금지) |
| `~/.ssh/id_ed25519.pub` | 공개키 (OCI에 등록) |

공개키 내용 복사:

```powershell
cat ~/.ssh/id_ed25519.pub
```

### macOS / Linux

```bash
ssh-keygen -t ed25519 -C "arbot"
cat ~/.ssh/id_ed25519.pub
```

### OCI에 등록

VM 인스턴스 생성 시 **"Add SSH keys"** 단계에서:

1. **Paste public keys** 선택
2. `~/.ssh/id_ed25519.pub` 내용 붙여넣기

::: warning
개인키(`id_ed25519`)는 절대 공유하지 마세요. 공개키(`.pub`)만 등록합니다.
:::

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
ssh ubuntu@<VM_PUBLIC_IP>

# 소스 클론
git clone https://github.com/geniuskey/arbot.git ~/arbot
cd ~/arbot

# 시스템 패키지 설치 (uv, Python 3.12, PostgreSQL, Redis, Prometheus, Grafana)
chmod +x deploy/oracle-cloud/setup.sh
./deploy/oracle-cloud/setup.sh
```

## Step 3: 환경 변수 설정

```bash
cp .env.example .env
nano .env
```

최소 필수 설정:

```env
POSTGRES_PASSWORD=<strong_password>
ARBOT_DATABASE__POSTGRES__PASSWORD=<strong_password>
```

::: tip 비밀번호 생성
```bash
openssl rand -base64 24
```
:::

## Step 4: ArBot 설치

```bash
chmod +x deploy/oracle-cloud/install.sh
./deploy/oracle-cloud/install.sh
```

이 스크립트가 자동으로:
- PostgreSQL DB/유저 생성
- `uv venv` + `uv pip install` (pip 대비 10-100x 빠름)
- systemd 서비스 등록

## Step 5: 시작 및 확인

```bash
# 시작
sudo systemctl start arbot

# 로그 확인
journalctl -u arbot -f

# 상태 확인
sudo systemctl status arbot
```

### Grafana 접속

브라우저에서 `http://<VM_PUBLIC_IP>:3000` 접속

- ID: `admin`
- PW: 초기 비밀번호 `admin` (첫 로그인 시 변경)

## 운영 명령어

```bash
# 시작 / 중지 / 재시작
sudo systemctl start arbot
sudo systemctl stop arbot
sudo systemctl restart arbot

# 실시간 로그
journalctl -u arbot -f

# 최근 100줄
journalctl -u arbot -n 100

# 서비스 상태
systemctl status arbot postgresql redis-server prometheus grafana-server
```

## 업데이트

```bash
cd ~/arbot && git pull && uv pip install --python .venv/bin/python . && sudo systemctl restart arbot
```

1줄로 끝. uv는 의존성 설치가 수초 만에 완료.

## 자동 재시작

systemd `Restart=always` 설정으로 크래시 시 5초 후 자동 재시작. VM 재부팅 시에도 자동 시작.

```bash
# 부팅 시 자동 시작 확인
sudo systemctl is-enabled arbot
```

## 메모리 배분

24GB RAM 사용 (Docker 오버헤드 없음):

| 서비스 | 예상 사용량 | 비고 |
|--------|-----------|------|
| ArBot | 1-2 GB | 메인 봇 |
| PostgreSQL | 1-2 GB | 거래 기록 |
| Redis | 0.5 GB | 캐시 |
| Prometheus | 0.3 GB | 메트릭 |
| Grafana | 0.3 GB | 대시보드 |
| OS + 여유 | ~19 GB | 충분한 버퍼 |
