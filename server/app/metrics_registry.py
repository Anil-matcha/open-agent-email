"""Process-local counters and gauges for the /metrics endpoint.

Values live in memory and reset on restart, which is what Prometheus counters expect. With several server
processes, scrape each one and sum the counters; database-derived gauges (queue depth) are identical everywhere.
"""
from collections import defaultdict

_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_gauges: dict[str, float] = {}


def inc(name: str, amount: float = 1.0, **labels: str) -> None:
    _counters[(name, tuple(sorted(labels.items())))] += amount


def set_gauge(name: str, value: float) -> None:
    _gauges[name] = value


def gauge(name: str) -> float | None:
    return _gauges.get(name)


def counters(name: str) -> dict[tuple[tuple[str, str], ...], float]:
    return {labels: value for (n, labels), value in _counters.items() if n == name}


def reset() -> None:
    _counters.clear()
    _gauges.clear()
