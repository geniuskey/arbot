# 리스크 관리

## 리스크 관리 철학

차익거래는 "무위험 수익"으로 알려져 있지만, 실제로는 실행 리스크, 시장 리스크, 기술 리스크 등 다양한 위험이 존재합니다. ArBot의 리스크 관리 시스템은 다음 원칙을 따릅니다:

- **자본 보존 우선**: 수익 기회보다 손실 방지를 우선시합니다
- **다중 방어선**: 포지션 제한, 드로다운 모니터링, 이상 감지, 서킷 브레이커가 단계적으로 작동합니다
- **자동화된 보호**: 인간의 개입 없이 자동으로 위험을 차단합니다
- **보수적 기본값**: 기본 설정은 보수적으로 구성되어 있으며, 운영 경험에 따라 점진적으로 조정합니다

## 리스크 관리 모듈 구조

```
risk/
├── manager.py           # 리스크 관리 메인 엔진
├── position_limits.py   # 포지션 크기 제한
├── drawdown_monitor.py  # 드로다운 모니터링
├── anomaly_detector.py  # 이상 가격 감지
└── circuit_breaker.py   # 서킷 브레이커 (긴급 정지)
```

## 포지션 제한 설정

포지션 제한은 3단계로 구성됩니다:

### 코인별 제한

단일 코인에 대한 최대 포지션을 제한합니다.

```yaml
risk:
  max_position_per_coin_usd: 10000    # 코인당 최대 $10,000
```

### 거래소별 제한

단일 거래소에 집중되는 리스크를 방지합니다.

```yaml
risk:
  max_position_per_exchange_usd: 50000  # 거래소당 최대 $50,000
```

### 전체 노출 제한

전체 포트폴리오의 최대 노출을 제한합니다.

```yaml
risk:
  max_total_exposure_usd: 100000      # 전체 최대 $100,000
```

## 드로다운 모니터링

드로다운은 포트폴리오의 최고점 대비 현재까지의 하락폭을 의미합니다. 설정된 임계값을 초과하면 모든 거래가 중단됩니다.

```yaml
risk:
  max_drawdown_pct: 5.0               # 최대 드로다운 5%
  warning_threshold_pct: 70.0         # 임계값의 70% 도달 시 경고
```

동작 방식:
1. 포트폴리오 가치의 최고점(High Water Mark)을 지속적으로 추적합니다
2. 현재 가치가 최고점 대비 `max_drawdown_pct` 이상 하락하면 거래를 중단합니다
3. `warning_threshold_pct`에 도달하면 경고 알림을 발송합니다

예시: 초기 자본 $100,000에서 최고점 $105,000 달성 후 $99,750까지 하락하면 드로다운 5%로 거래 중단.

## 이상 가격 감지

시장에서 발생하는 비정상적인 가격 변동을 감지하여 잘못된 거래를 방지합니다.

### Flash Crash 감지

```yaml
risk:
  flash_crash_pct: 10.0               # 10% 이상 급변 시 감지
```

단기간에 가격이 급격히 변동하는 경우를 감지합니다. Flash Crash 시 잘못된 가격으로 주문이 체결될 위험이 있으므로, 해당 심볼의 거래를 일시 중단합니다.

### 비정상 스프레드 감지

```yaml
risk:
  max_spread_pct: 5.0                 # 최대 허용 스프레드 5%
  spread_std_threshold: 3.0           # 스프레드 표준편차 3배 초과 시 감지
```

거래소 간 스프레드가 비정상적으로 큰 경우, 한쪽 거래소의 데이터 오류이거나 유동성 부족일 가능성이 높습니다. 이런 시그널은 자동으로 필터링됩니다.

### 가격 이탈 감지

```yaml
risk:
  price_deviation_threshold_pct: 10.0 # 기준 가격 대비 10% 이상 이탈 시 감지
```

### 데이터 신선도 확인

```yaml
risk:
  stale_threshold_seconds: 30.0       # 30초 이상 미갱신 데이터는 무시
```

오래된 데이터로 인한 잘못된 시그널을 방지합니다. 거래소 WebSocket 연결이 불안정할 때 특히 중요합니다.

## 서킷 브레이커

연속 손실이 발생하면 자동으로 거래를 중단하고 일정 시간 대기하는 안전장치입니다.

```yaml
risk:
  consecutive_loss_limit: 10          # 10회 연속 손실 시 정지
  cooldown_minutes: 30                # 30분 대기 후 재개
```

동작 흐름:
1. 연속 손실 횟수를 카운팅합니다
2. `consecutive_loss_limit` 도달 시 모든 신규 거래를 차단합니다
3. `cooldown_minutes` 동안 대기합니다
4. 대기 종료 후 연속 손실 카운터를 리셋하고 거래를 재개합니다
5. 서킷 브레이커 발동 시 알림을 발송합니다

::: tip
서킷 브레이커가 자주 발동된다면, 전략 파라미터나 시장 상황을 재검토해야 합니다. 특히 `min_spread_pct` 값이 너무 낮거나 시장 변동성이 급격히 변한 경우에 발생할 수 있습니다.
:::

## 일일 손실 제한

하루 동안 발생할 수 있는 최대 손실을 제한합니다.

```yaml
risk:
  max_daily_loss_usd: 500             # 일일 최대 $500 손실
  max_daily_loss_pct: 1.0             # 일일 최대 1% 손실
```

둘 중 하나라도 초과하면 해당 일의 모든 거래가 중단됩니다. UTC 기준 자정에 리셋됩니다.

## 파라미터 튜닝 가이드

리스크 파라미터는 운영 환경과 경험에 따라 조정해야 합니다:

### 보수적 설정 (초기 운영 권장)

```yaml
risk:
  max_position_per_coin_usd: 5000
  max_position_per_exchange_usd: 20000
  max_total_exposure_usd: 50000
  max_daily_loss_usd: 200
  max_daily_loss_pct: 0.5
  max_drawdown_pct: 3.0
  consecutive_loss_limit: 5
  cooldown_minutes: 60
```

### 표준 설정 (안정적 운영 확인 후)

```yaml
risk:
  max_position_per_coin_usd: 10000
  max_position_per_exchange_usd: 50000
  max_total_exposure_usd: 100000
  max_daily_loss_usd: 500
  max_daily_loss_pct: 1.0
  max_drawdown_pct: 5.0
  consecutive_loss_limit: 10
  cooldown_minutes: 30
```

### 적극적 설정 (충분한 운영 경험 후)

```yaml
risk:
  max_position_per_coin_usd: 20000
  max_position_per_exchange_usd: 100000
  max_total_exposure_usd: 200000
  max_daily_loss_usd: 1000
  max_daily_loss_pct: 2.0
  max_drawdown_pct: 8.0
  consecutive_loss_limit: 15
  cooldown_minutes: 15
```

::: warning
적극적 설정은 최소 3개월 이상의 안정적 운영 이력이 있는 경우에만 사용하세요.
:::

## RiskConfig 전체 파라미터 테이블

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `max_position_per_coin_usd` | float | 10,000 | 단일 코인 최대 포지션 ($) |
| `max_position_per_exchange_usd` | float | 50,000 | 단일 거래소 최대 포지션 ($) |
| `max_total_exposure_usd` | float | 100,000 | 전체 포트폴리오 최대 노출 ($) |
| `max_daily_loss_usd` | float | 500 | 일일 최대 손실 금액 ($) |
| `max_daily_loss_pct` | float | 1.0 | 일일 최대 손실률 (%) |
| `max_drawdown_pct` | float | 5.0 | 최대 허용 드로다운 (%) |
| `price_deviation_threshold_pct` | float | 10.0 | 이상 가격 감지 임계값 (%) |
| `max_spread_pct` | float | 5.0 | 비정상 스프레드 임계값 (%) |
| `flash_crash_pct` | float | 10.0 | Flash Crash 감지 임계값 (%) |
| `spread_std_threshold` | float | 3.0 | 스프레드 표준편차 배수 |
| `stale_threshold_seconds` | float | 30.0 | 데이터 신선도 임계값 (초) |
| `warning_threshold_pct` | float | 70.0 | 경고 발송 임계값 (한도의 %) |
| `consecutive_loss_limit` | int | 10 | 연속 손실 허용 횟수 |
| `cooldown_minutes` | int | 30 | 서킷 브레이커 대기 시간 (분) |
