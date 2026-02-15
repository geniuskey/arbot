# 테스트 가이드

ArBot의 테스트 전략, 실행 방법, 코드 품질 도구를 설명합니다.

## 테스트 프레임워크

| 도구 | 버전 | 용도 |
|------|------|------|
| `pytest` | >= 8.0 | 테스트 프레임워크 |
| `pytest-asyncio` | >= 0.23 | 비동기 테스트 지원 |
| `pytest-cov` | - | 코드 커버리지 측정 |
| `fakeredis` | - | Redis 목킹 |

### 설치

```bash
pip install -e ".[dev]"
```

`pyproject.toml`의 `[project.optional-dependencies]` > `dev`에 모든 테스트 의존성이 정의되어 있습니다.

## 테스트 실행

### 전체 테스트

```bash
pytest
```

### 커버리지 포함

```bash
pytest --cov=arbot --cov-report=html
```

커버리지 리포트는 `htmlcov/` 디렉토리에 생성됩니다.

### 특정 모듈만

```bash
# 단위 테스트만
pytest tests/unit/

# 특정 파일
pytest tests/unit/test_spread_calculator.py

# 특정 테스트 함수
pytest tests/unit/test_spread_calculator.py::test_net_spread_calculation
```

### 상세 출력

```bash
pytest -v
```

### 실패한 테스트만 재실행

```bash
pytest --lf
```

## 테스트 구조

```
tests/
├── unit/                   # 단위 테스트
│   ├── test_models.py      # Pydantic 모델 테스트
│   ├── test_spread_calculator.py
│   ├── test_spatial_detector.py
│   ├── test_risk_manager.py
│   ├── test_circuit_breaker.py
│   └── ...
├── integration/            # 통합 테스트
│   ├── test_connector_redis.py
│   ├── test_detector_executor.py
│   ├── test_storage.py
│   └── ...
└── e2e/                    # E2E 테스트
    ├── test_backtest_pipeline.py
    └── test_paper_trading.py
```

### 단위 테스트 (Unit)

개별 함수와 클래스를 독립적으로 테스트합니다.

```python
# tests/unit/test_spread_calculator.py
def test_net_spread_includes_fees():
    """수수료 반영 후 순스프레드 계산"""
    result = calculate_net_spread(
        buy_price=100.0,
        sell_price=101.0,
        buy_fee_pct=0.1,
        sell_fee_pct=0.1,
    )
    assert result < 1.0  # 수수료 차감 후 스프레드 < 1%
```

### 통합 테스트 (Integration)

모듈 간 상호작용을 테스트합니다. Docker 서비스(PostgreSQL, Redis 등)가 필요할 수 있습니다.

```python
# tests/integration/test_detector_executor.py
async def test_signal_to_execution():
    """탐지 시그널이 실행 엔진까지 전달되는지 확인"""
    detector = SpatialDetector(config)
    executor = PaperExecutor()

    signals = detector.detect(mock_orderbooks)
    for signal in signals:
        result = await executor.execute(signal)
        assert result.status == "FILLED"
```

### E2E 테스트

전체 파이프라인을 백테스트 모드로 검증합니다.

```python
# tests/e2e/test_backtest_pipeline.py
async def test_full_backtest_pipeline():
    """백테스팅 전체 파이프라인 E2E 테스트"""
    engine = BacktestEngine(config)
    result = await engine.run(backtest_config)

    assert result.total_trades > 0
    assert result.sharpe_ratio is not None
```

## pytest 설정

`pyproject.toml`에 다음과 같이 설정되어 있습니다:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- `testpaths`: `tests/` 디렉토리를 테스트 루트로 설정
- `asyncio_mode = "auto"`: `async def` 테스트 함수를 자동으로 비동기 실행

## 목킹 전략

### fakeredis

Redis 의존성 없이 테스트할 수 있습니다.

```python
import fakeredis

@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()

def test_price_cache(redis_client):
    redis_client.set("price:BTC/USDT:binance", "50000.0")
    assert redis_client.get("price:BTC/USDT:binance") == b"50000.0"
```

### 거래소 API 목킹

실제 거래소 API 호출 없이 테스트합니다.

```python
@pytest.fixture
def mock_connector():
    connector = MagicMock(spec=BaseConnector)
    connector.get_balances.return_value = {
        "USDT": Balance(free=10000.0, locked=0.0),
    }
    return connector
```

### 오더북 목킹

```python
@pytest.fixture
def mock_orderbook():
    return OrderBook(
        exchange="binance",
        symbol="BTC/USDT",
        bids=[(50000.0, 1.0), (49999.0, 2.0)],
        asks=[(50001.0, 1.0), (50002.0, 2.0)],
        timestamp=time.time(),
    )
```

## 코드 품질 도구

### ruff (포매터 + 린터)

```bash
# 린트 체크
ruff check src/ tests/

# 자동 수정
ruff check --fix src/ tests/

# 포맷 체크
ruff format --check src/ tests/

# 자동 포맷
ruff format src/ tests/
```

`pyproject.toml` 설정:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]
```

- `E`, `W`: pycodestyle 에러/경고
- `F`: pyflakes
- `I`: isort (import 정렬)
- `N`: pep8-naming
- `UP`: pyupgrade (Python 3.12+ 문법)

### mypy (타입 체크)

```bash
mypy src/arbot/
```

`pyproject.toml` 설정:

```toml
[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_configs = true
```

- `strict = true`: 모든 strict 옵션 활성화
- 모든 함수에 타입 힌트 필수
- `Any` 타입 사용 시 경고

## CI에서의 테스트

GitHub Actions에서 PR마다 자동으로 테스트가 실행됩니다.

```yaml
# CI 파이프라인 (예시)
steps:
  - name: Install dependencies
    run: pip install -e ".[dev]"

  - name: Lint
    run: ruff check src/ tests/

  - name: Type check
    run: mypy src/arbot/

  - name: Test
    run: pytest --cov=arbot
```

테스트, 린트, 타입 체크 모두 통과해야 PR 머지가 가능합니다.
