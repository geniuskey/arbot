# 환경 변수

ArBot은 `.env` 파일을 통해 민감한 설정값을 관리합니다. 거래소 API 키, 데이터베이스 비밀번호, 알림 토큰 등은 모두 환경 변수로 설정합니다.

## .env 파일 설정

### 1. 템플릿 복사

```bash
cp .env.example .env
```

### 2. 값 채우기

```bash
# 에디터로 .env 파일 편집
vim .env
```

## 환경 변수 목록

### 마스터 키

| 변수 | 설명 | 필수 |
|------|------|------|
| `ARBOT_MASTER_KEY` | API 키 AES-256 암호화 마스터 키 | Yes |

마스터 키는 모든 거래소 API 키를 암호화/복호화하는 데 사용됩니다. 안전한 랜덤 문자열을 생성하여 설정하세요.

```bash
# 마스터 키 생성 (Python)
python -c "import secrets; print(secrets.token_hex(32))"
```

### 거래소 API 키

#### Binance

| 변수 | 설명 | 필수 |
|------|------|------|
| `BINANCE_API_KEY` | Binance API 키 | Yes |
| `BINANCE_API_SECRET` | Binance API 시크릿 | Yes |
| `BINANCE_IP_WHITELIST` | 허용 IP 목록 (쉼표 구분) | 권장 |

#### OKX

| 변수 | 설명 | 필수 |
|------|------|------|
| `OKX_API_KEY` | OKX API 키 | Yes |
| `OKX_API_SECRET` | OKX API 시크릿 | Yes |
| `OKX_PASSPHRASE` | OKX API 패스프레이즈 | Yes |

#### Bybit

| 변수 | 설명 | 필수 |
|------|------|------|
| `BYBIT_API_KEY` | Bybit API 키 | Yes |
| `BYBIT_API_SECRET` | Bybit API 시크릿 | Yes |

#### Upbit

| 변수 | 설명 | 필수 |
|------|------|------|
| `UPBIT_API_KEY` | Upbit API 키 | 선택 |
| `UPBIT_API_SECRET` | Upbit API 시크릿 | 선택 |

#### KuCoin

| 변수 | 설명 | 필수 |
|------|------|------|
| `KUCOIN_API_KEY` | KuCoin API 키 | 선택 |
| `KUCOIN_API_SECRET` | KuCoin API 시크릿 | 선택 |
| `KUCOIN_PASSPHRASE` | KuCoin API 패스프레이즈 | 선택 |

### 데이터베이스

| 변수 | 설명 | 필수 |
|------|------|------|
| `POSTGRES_PASSWORD` | PostgreSQL 비밀번호 | Yes |
| `REDIS_PASSWORD` | Redis 비밀번호 | Yes |

### 알림

| 변수 | 설명 | 필수 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram 봇 토큰 | 선택 |
| `TELEGRAM_CHAT_ID` | Telegram 채팅 ID | 선택 |

## .env.example 전체

```bash
# ArBot Environment Variables
# Copy to .env and fill in values

# Master encryption key for API key storage
ARBOT_MASTER_KEY=

# Binance
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_IP_WHITELIST=

# OKX
OKX_API_KEY=
OKX_API_SECRET=
OKX_PASSPHRASE=

# Bybit
BYBIT_API_KEY=
BYBIT_API_SECRET=

# Upbit
UPBIT_API_KEY=
UPBIT_API_SECRET=

# KuCoin
KUCOIN_API_KEY=
KUCOIN_API_SECRET=
KUCOIN_PASSPHRASE=

# Database
POSTGRES_PASSWORD=
REDIS_PASSWORD=

# Alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

## API 키 보안

### AES-256 암호화

ArBot은 `cryptography` 라이브러리를 사용하여 거래소 API 키를 AES-256으로 암호화합니다.

- **`ARBOT_MASTER_KEY`**: 모든 API 키를 암호화/복호화하는 마스터 키
- API 키는 메모리에서만 복호화되며, 디스크에는 암호화된 상태로 저장
- 마스터 키 분실 시 API 키 복구 불가 - 안전한 곳에 백업 필수

### 거래소별 API 키 발급

#### Binance
1. [Binance API Management](https://www.binance.com/en/my/settings/api-management) 접속
2. API 키 생성 - **거래 권한만 활성화** (출금 권한 비활성화)
3. IP 화이트리스트 설정

#### OKX
1. [OKX API 설정](https://www.okx.com/account/my-api) 접속
2. API 키 생성 - 패스프레이즈 설정 필수
3. **거래 권한만 활성화**, IP 제한 설정

#### Bybit
1. [Bybit API Management](https://www.bybit.com/app/user/api-management) 접속
2. API 키 생성 - 거래 권한 설정
3. IP 화이트리스트 설정

#### Upbit
1. [Upbit Open API](https://upbit.com/mypage/open_api_management) 접속
2. API 키 생성 - **주문 허용**, 출금 비허용
3. 허용 IP 설정

## IP 화이트리스트 설정

모든 거래소에서 API 키를 사용할 IP 주소를 화이트리스트로 제한하세요.

```bash
# 현재 서버의 공인 IP 확인
curl ifconfig.me

# .env에 IP 설정
BINANCE_IP_WHITELIST=203.0.113.1,203.0.113.2
```

::: tip 권장 사항
- 서버의 고정 IP만 등록
- VPN이나 프록시를 사용하는 경우 해당 IP도 등록
- IP가 변경되면 즉시 업데이트
:::

## 주의사항

::: danger .env 파일 보안
- `.env` 파일은 **절대 Git에 커밋하지 마세요** - `.gitignore`에 이미 포함되어 있습니다
- `.env` 파일의 권한을 제한하세요: `chmod 600 .env`
- 팀원 간 공유 시 암호화된 채널(1Password, Vault 등)을 사용하세요
- 출금 권한이 있는 API 키는 **절대 사용하지 마세요**
:::

- 모든 거래소 API 키는 **거래 전용**으로 발급하세요 (출금 권한 비활성화)
- 프로덕션 환경에서는 반드시 강력한 비밀번호를 사용하세요
- 마스터 키(`ARBOT_MASTER_KEY`)는 별도의 안전한 저장소에 백업하세요
- 정기적으로 API 키를 로테이션하는 것을 권장합니다
