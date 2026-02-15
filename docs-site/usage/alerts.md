# 알림 설정

## 알림 시스템 개요

ArBot은 Telegram과 Discord를 통해 실시간 알림을 발송합니다. 차익거래 기회 탐지, 일일 성과 요약, 에러 및 장애 상황을 즉시 전달받을 수 있습니다.

알림 설정은 `configs/default.yaml`의 `alerts` 섹션에서 관리합니다:

```yaml
alerts:
  telegram:
    enabled: true
    chat_id: ""
    bot_token: ""
  discord:
    enabled: false
    bot_token: ""
    guild_id: 0
    channel_id: 0
  thresholds:
    opportunity_min_pct: 0.5
    daily_pnl_alert: true
    error_alert: true
```

## Telegram 봇 설정

### 1. BotFather로 봇 생성

1. Telegram에서 [@BotFather](https://t.me/BotFather)를 검색합니다
2. `/newbot` 명령어를 전송합니다
3. 봇 이름과 사용자명을 입력합니다 (예: `ArBot Alerts`, `arbot_alerts_bot`)
4. BotFather가 발급하는 **Bot Token**을 복사합니다

### 2. Chat ID 확인

1. 생성한 봇과 대화를 시작합니다 (아무 메시지 전송)
2. 다음 URL을 브라우저에서 열어 Chat ID를 확인합니다:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
   ```
3. 응답 JSON에서 `result[0].message.chat.id` 값을 확인합니다
4. 그룹 채팅을 사용하는 경우 Chat ID는 음수 값입니다

### 3. 설정 적용

```yaml
alerts:
  telegram:
    enabled: true
    chat_id: "123456789"           # 확인한 Chat ID
    bot_token: ""                  # .env 파일에서 관리 권장
```

보안을 위해 Bot Token은 `.env` 파일에 저장하는 것을 권장합니다:

```bash
# .env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

## Discord 봇 설정

### 1. Discord 봇 생성

1. [Discord Developer Portal](https://discord.com/developers/applications)에 접속합니다
2. **New Application**을 클릭하고 앱 이름을 입력합니다 (예: `ArBot`)
3. **Bot** 탭에서 **Add Bot**을 클릭합니다
4. **Token**을 복사합니다 (Reset Token으로 새로 발급 가능)
5. **MESSAGE CONTENT INTENT**를 활성화합니다

### 2. 봇 서버 초대

1. **OAuth2 > URL Generator**에서 다음 Scope를 선택합니다:
   - `bot`
   - `applications.commands`
2. Bot Permissions에서 **Send Messages**를 선택합니다
3. 생성된 URL로 봇을 서버에 초대합니다

### 3. Guild ID / Channel ID 확인

1. Discord 설정에서 **개발자 모드**를 활성화합니다 (사용자 설정 > 고급)
2. 서버 이름을 우클릭하여 **서버 ID 복사** (Guild ID)
3. 알림을 받을 채널을 우클릭하여 **채널 ID 복사** (Channel ID)

### 4. 설정 적용

```yaml
alerts:
  discord:
    enabled: true
    bot_token: ""                  # .env 파일에서 관리
    guild_id: 123456789012345678
    channel_id: 987654321098765432
```

```bash
# .env
DISCORD_BOT_TOKEN=MTIzNDU2Nzg5MDEy...
```

## 알림 유형

### 기회 탐지 알림

차익거래 기회가 설정된 임계값 이상으로 탐지되면 알림을 발송합니다.

알림 내용:
- 전략 유형 (Spatial / Triangular / Statistical)
- 매수/매도 거래소
- 심볼 및 가격
- 스프레드 (%)
- 예상 수익 ($)

예시:
```
[ArBot] 차익거래 기회 탐지
전략: Spatial
매수: Binance BTC/USDT @ $67,150.00
매도: Upbit BTC/USDT @ $67,520.00
스프레드: 0.55%
예상 수익: $37.00
```

### 일일 PnL 알림

매일 UTC 자정에 당일 성과 요약을 발송합니다.

알림 내용:
- 일일 순 PnL ($)
- 누적 PnL ($)
- 거래 횟수
- 승률
- 최대 드로다운

예시:
```
[ArBot] 일일 성과 요약 (2026-02-14)
순 PnL: +$127.50
누적 PnL: +$2,340.80
거래: 45회 (승률 68.9%)
최대 드로다운: 1.2%
```

### 에러/장애 알림

시스템 장애 및 에러 발생 시 즉시 알림을 발송합니다.

알림 대상:
- 거래소 WebSocket 연결 끊김
- 주문 실행 실패
- 서킷 브레이커 발동
- 데이터베이스 연결 실패
- 드로다운 경고/한도 도달
- 일일 손실 한도 도달

예시:
```
[ArBot] 서킷 브레이커 발동
연속 손실: 10회
대기 시간: 30분
재개 예정: 14:30 UTC
```

## 알림 임계값 설정

### 기회 탐지 임계값

```yaml
alerts:
  thresholds:
    opportunity_min_pct: 0.5       # 0.5% 이상 스프레드만 알림
```

이 값을 낮추면 더 많은 알림을 받지만, 작은 기회도 포함됩니다. 높이면 의미 있는 기회만 알림을 받습니다.

### 일일 PnL 알림

```yaml
alerts:
  thresholds:
    daily_pnl_alert: true          # 일일 성과 요약 활성화
```

### 에러 알림

```yaml
alerts:
  thresholds:
    error_alert: true              # 에러/장애 알림 활성화
```

## 스로틀링 설정

과도한 알림을 방지하기 위해 스로틀링이 적용됩니다.

### 스로틀링 동작 방식

- **기회 탐지 알림**: 동일한 거래소 페어-심볼 조합에 대해 일정 간격 이내의 중복 알림을 억제합니다
- **에러 알림**: 동일한 에러 유형에 대해 반복 알림을 억제합니다
- **서킷 브레이커 알림**: 발동/해제 시 각 1회만 알림을 발송합니다

::: tip
알림이 너무 많으면 `opportunity_min_pct`를 높여 의미 있는 기회만 알림을 받으세요. 반대로 알림이 없다면 임계값을 낮추거나, 거래소 연결 상태를 확인하세요.
:::

::: warning
Telegram과 Discord Bot Token은 반드시 `.env` 파일에 저장하고, 절대 설정 파일이나 소스 코드에 직접 입력하지 마세요. `.env` 파일은 `.gitignore`에 포함되어 있어 Git에 커밋되지 않습니다.
:::
