# 백테스팅

백테스팅은 히스토리컬 시장 데이터를 활용하여 차익거래 전략의 성과를 검증하는 과정입니다. 과거 데이터를 기반으로 전략이 어떤 성과를 냈을지 시뮬레이션하여, 실전 투입 전 전략의 유효성을 판단합니다.

## 백테스팅 개요

ArBot의 백테스팅 엔진은 다음 흐름으로 동작합니다:

1. **데이터 로딩**: 히스토리컬 오더북/체결 데이터를 ClickHouse에서 로딩
2. **이벤트 루프**: 틱 단위로 시장 상태를 재현하며 기회 탐지
3. **시뮬레이션 실행**: 리스크 체크 후 가상 체결 처리
4. **성과 계산**: 포트폴리오 기반 성과 지표 산출

```python
class BacktestEngine:
    async def run(self, config: BacktestConfig) -> BacktestResult:
        # 1. 데이터 로딩
        data = await self.data_loader.load(
            exchanges=config.exchanges,
            symbols=config.symbols,
            start=config.start_date,
            end=config.end_date,
        )

        # 2. 이벤트 루프
        for tick in data.iter_ticks():
            self.market.update(tick)
            signals = self.detector.detect(self.market.state)
            approved = self.risk_manager.check(signals)

            # 3. 시뮬레이션 실행
            for signal in approved:
                result = self.executor.execute(signal, self.market.orderbook)
                self.portfolio.update(result)

        # 4. 성과 계산
        return self.metrics.calculate(self.portfolio)
```

## 데이터 준비

### ClickHouse 히스토리컬 데이터

ArBot은 실시간 운영 중 수집된 오더북 스냅샷과 체결 데이터를 ClickHouse에 저장합니다. 이 데이터가 백테스팅의 기본 소스입니다.

- `orderbook_snapshots`: 오더북 스냅샷 (90일 TTL)
- `tick_trades`: 체결 데이터 (180일 TTL)
- `spread_history`: 사전 계산된 스프레드 데이터 (365일 TTL)

### ccxt OHLCV 데이터

ClickHouse에 충분한 데이터가 없는 경우, ccxt 라이브러리를 통해 거래소에서 OHLCV(시가/고가/저가/종가/거래량) 데이터를 직접 가져올 수 있습니다.

```bash
# 히스토리컬 데이터 수집 스크립트
python scripts/collect_historical.py \
  --exchanges binance okx bybit \
  --symbols BTC/USDT ETH/USDT \
  --start 2025-01-01 \
  --end 2025-12-31
```

## 백테스트 설정

`configs/default.yaml`에서 실행 모드를 `backtest`로 변경합니다:

```yaml
system:
  execution_mode: backtest
```

탐지 전략별 파라미터를 조정합니다:

```yaml
detector:
  spatial:
    enabled: true
    min_spread_pct: 0.25       # 최소 스프레드 (%)
    min_depth_usd: 1000        # 최소 유동성 ($)
    max_latency_ms: 500        # 최대 허용 레이턴시

  triangular:
    enabled: true
    min_profit_pct: 0.15

  statistical:
    enabled: false
    lookback_periods: 60
    entry_zscore: 2.0
    exit_zscore: 0.5
    p_value_threshold: 0.05
```

## 백테스트 실행

```bash
# 기본 백테스트 실행
arbot --config configs/default.yaml

# 또는 모듈로 직접 실행
python -m arbot.main
```

## 성과 지표

백테스트 완료 후 다음 지표가 산출됩니다:

### 수익성 지표

| 지표 | 설명 |
|------|------|
| **Total PnL** | 전체 기간 누적 순손익 (수수료 차감 후) |
| **Net PnL** | 수수료 차감 후 순수익 |
| **Win Rate** | 전체 거래 중 수익 거래 비율 (%) |
| **Profit Factor** | 총 수익 / 총 손실. 1.0 이상이면 수익, 2.0 이상이면 우수 |

### 리스크 지표

| 지표 | 설명 |
|------|------|
| **Sharpe Ratio** | 위험 대비 수익률. 무위험 수익률 대비 초과 수익의 표준편차 비율. 1.0 이상 양호, 2.0 이상 우수 |
| **Max Drawdown** | 최고점 대비 최대 하락폭 (%). 전략의 최악 시나리오 손실을 나타냄 |
| **Total Signals** | 탐지된 차익거래 시그널 수 |
| **Executed Trades** | 실제 실행된 거래 수 |

## 리포트 해석

백테스트 결과는 `daily_performance` 테이블에 일별로 기록됩니다.

```bash
# 리포트 생성
python scripts/generate_report.py
```

리포트 확인 시 주요 포인트:

- **일별 PnL 추이**: 꾸준한 수익이 발생하는지, 특정 날에 큰 손실이 집중되는지 확인
- **시간대별 패턴**: 특정 시간대에 기회가 집중되는지 분석
- **거래소 페어별 성과**: 어떤 거래소 조합이 가장 수익성이 높은지 파악
- **전략별 기여도**: Spatial, Triangular, Statistical 전략 각각의 성과 비교

## Walk-Forward 분석

단순 백테스트는 과최적화 위험이 있습니다. Walk-Forward 분석은 이를 방지하기 위한 기법입니다:

1. **학습 구간 (In-Sample)**: 파라미터를 최적화하는 구간
2. **검증 구간 (Out-of-Sample)**: 최적화된 파라미터로 성과를 검증하는 구간
3. **롤링 윈도우**: 학습/검증 구간을 시간순으로 이동하며 반복

예를 들어, 3개월 학습 + 1개월 검증을 6회 반복하여 전체 9개월을 커버할 수 있습니다. 모든 검증 구간에서 일관된 성과가 나타나면 전략이 견고하다고 판단할 수 있습니다.

## 과최적화 주의사항

::: warning 과최적화(Overfitting) 경고
과최적화는 백테스팅에서 가장 흔한 함정입니다. 과거 데이터에 과도하게 맞추어진 전략은 실전에서 성과가 급격히 하락합니다.
:::

과최적화를 방지하기 위한 가이드라인:

- **파라미터 수 최소화**: 조정 가능한 파라미터가 많을수록 과최적화 위험이 증가합니다
- **충분한 데이터 기간**: 최소 6개월 이상의 데이터로 백테스트하세요
- **다양한 시장 상황 포함**: 상승장, 하락장, 횡보장 데이터가 모두 포함되어야 합니다
- **Out-of-Sample 검증 필수**: 학습에 사용하지 않은 데이터로 반드시 검증하세요
- **거래 횟수 확인**: 통계적으로 유의미한 결론을 내리려면 최소 100회 이상의 거래가 필요합니다
- **비용 현실적 반영**: 수수료, 슬리피지, 전송 비용을 현실적으로 설정하세요
