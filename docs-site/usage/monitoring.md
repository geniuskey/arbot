# 모니터링

## 모니터링 스택 개요

ArBot은 **Prometheus + Grafana** 조합을 사용하여 시스템 상태와 트레이딩 성과를 실시간으로 모니터링합니다.

```
ArBot (prometheus-client)
    │
    ▼ 메트릭 수집
Prometheus (포트 9090)
    │
    ▼ 데이터 소스
Grafana (포트 3000)
    │
    ▼ 시각화 + 알림
대시보드 / 알림 규칙
```

## Prometheus 설정

### 메트릭 수집

ArBot은 `prometheus-client` 라이브러리를 사용하여 메트릭을 노출합니다. 메트릭 엔드포인트는 기본적으로 `/metrics` 경로에서 제공됩니다.

### prometheus.yml 설정

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'arbot'
    static_configs:
      - targets: ['arbot:8000']
    scrape_interval: 5s

  - job_name: 'node-exporter'
    static_configs:
      - targets: ['node-exporter:9100']
```

ArBot의 메트릭은 5초 간격으로 수집하는 것을 권장합니다. 트레이딩 시스템 특성상 빠른 상태 변화를 포착해야 하기 때문입니다.

## Grafana 대시보드 설정

### Docker Compose로 실행

`docker-compose.yml`에 이미 Grafana와 Prometheus가 포함되어 있습니다:

```yaml
services:
  grafana:
    image: grafana/grafana:11
    ports: ["3000:3000"]

  prometheus:
    image: prom/prometheus:v2
    ports: ["9090:9090"]
```

```bash
# 모니터링 스택 시작
docker compose up -d grafana prometheus
```

### 초기 접속

1. 브라우저에서 `http://localhost:3000` 접속
2. 기본 로그인: `admin` / `admin` (최초 접속 시 비밀번호 변경 권장)
3. **Configuration > Data Sources**에서 Prometheus 추가
   - URL: `http://prometheus:9090`
   - Access: Server (Default)

### 대시보드 구성

Grafana에서 ArBot 전용 대시보드를 구성합니다. 다음 패널을 권장합니다:

**시스템 상태 패널**
- 거래소 연결 상태 (연결/끊김)
- 시스템 가동 시간 (Uptime)
- 메모리/CPU 사용량

**트레이딩 성과 패널**
- 누적 PnL 차트
- 일별 PnL 바 차트
- 승률 게이지
- Sharpe Ratio 추이

**시그널 패널**
- 탐지된 시그널 수 (시간별)
- 전략별 시그널 비율 (Spatial / Triangular / Statistical)
- 시그널 실행률

## 주요 모니터링 지표

### 거래소 연결 상태

| 지표 | 설명 | 알림 조건 |
|------|------|----------|
| `arbot_exchange_connected` | 거래소 WebSocket 연결 여부 (0/1) | 0이면 즉시 알림 |
| `arbot_exchange_reconnects_total` | 재연결 횟수 | 5분 내 3회 이상 |
| `arbot_websocket_messages_total` | 수신 메시지 수 | 1분간 0이면 알림 |

### 레이턴시

| 지표 | 설명 | 알림 조건 |
|------|------|----------|
| `arbot_exchange_latency_ms` | 거래소 API 응답 시간 | 500ms 이상 |
| `arbot_order_execution_latency_ms` | 주문 실행 레이턴시 | 1000ms 이상 |
| `arbot_tick_processing_latency_ms` | 틱 데이터 처리 시간 | 100ms 이상 |

### 시그널 탐지

| 지표 | 설명 | 알림 조건 |
|------|------|----------|
| `arbot_signals_detected_total` | 탐지된 시그널 수 | - |
| `arbot_signals_executed_total` | 실행된 시그널 수 | - |
| `arbot_signals_rejected_total` | 리스크 체크 거부 수 | 거부율 80% 이상 |
| `arbot_signal_spread_pct` | 탐지된 스프레드 (%) | - |

### PnL

| 지표 | 설명 | 알림 조건 |
|------|------|----------|
| `arbot_pnl_total_usd` | 누적 PnL ($) | - |
| `arbot_pnl_daily_usd` | 일일 PnL ($) | 일일 손실 한도 도달 |
| `arbot_drawdown_pct` | 현재 드로다운 (%) | 5% 초과 |
| `arbot_win_rate` | 승률 | 50% 미만 |

### 에러율

| 지표 | 설명 | 알림 조건 |
|------|------|----------|
| `arbot_errors_total` | 에러 발생 횟수 | 5분 내 10회 이상 |
| `arbot_order_failures_total` | 주문 실패 횟수 | 연속 3회 이상 |
| `arbot_circuit_breaker_active` | 서킷 브레이커 상태 (0/1) | 1이면 알림 |

## 알림 규칙 설정

Grafana에서 알림 규칙을 설정하여 이상 상황을 즉시 감지합니다.

### 거래소 연결 끊김 알림

```yaml
# Grafana Alert Rule
alert: ExchangeDisconnected
expr: arbot_exchange_connected == 0
for: 1m
labels:
  severity: critical
annotations:
  summary: "거래소 연결 끊김"
  description: "{{ $labels.exchange }} 연결이 1분 이상 끊어져 있습니다"
```

### 일일 손실 한도 도달 알림

```yaml
alert: DailyLossLimitReached
expr: arbot_pnl_daily_usd < -500
for: 0m
labels:
  severity: critical
annotations:
  summary: "일일 손실 한도 도달"
  description: "일일 손실이 ${{ $value }}에 도달했습니다"
```

### 높은 에러율 알림

```yaml
alert: HighErrorRate
expr: rate(arbot_errors_total[5m]) > 2
for: 5m
labels:
  severity: warning
annotations:
  summary: "에러율 증가"
  description: "5분간 평균 에러율이 분당 {{ $value }}회입니다"
```

## 대시보드 커스터마이징

### PromQL 쿼리 예시

자주 사용하는 PromQL 쿼리:

```promql
# 거래소별 평균 레이턴시 (5분 이동 평균)
avg_over_time(arbot_exchange_latency_ms[5m])

# 시간당 시그널 탐지 수
rate(arbot_signals_detected_total[1h]) * 3600

# 전략별 시그널 비율
arbot_signals_detected_total / ignoring(strategy) group_left sum(arbot_signals_detected_total)

# 드로다운 추이
arbot_drawdown_pct

# 시그널 실행률
rate(arbot_signals_executed_total[1h]) / rate(arbot_signals_detected_total[1h]) * 100
```

### 권장 대시보드 레이아웃

```
┌─────────────────────────────────────────────────┐
│  시스템 상태          │  거래소 연결 상태           │
│  (Uptime, CPU, Mem)  │  (연결/끊김 상태 표시)      │
├─────────────────────────────────────────────────┤
│  누적 PnL 차트 (시계열)                           │
├─────────────────────────────────────────────────┤
│  일별 PnL          │  승률 게이지  │  드로다운 (%)  │
├─────────────────────────────────────────────────┤
│  시그널 탐지/실행 추이  │  거래소별 레이턴시           │
├─────────────────────────────────────────────────┤
│  에러 로그 (최근 50건)                             │
└─────────────────────────────────────────────────┘
```

::: tip
Grafana 대시보드 JSON 파일을 `configs/grafana/` 디렉토리에 저장해두면, 환경을 재구성할 때 쉽게 복원할 수 있습니다.
:::
