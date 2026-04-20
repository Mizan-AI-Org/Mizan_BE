"""
Forecasting helpers for the prep-list engine.

This module isolates the pure maths (EWMA mean, coefficient-of-variation,
EatNow covers overlay, pack rounding) from the POS integration plumbing so
the logic is testable and can be reused by Miya agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Weights for the last 4 weeks (week-1 = most recent) — geometric decay.
# Sum is normalised at compute-time so the weighted mean remains unbiased.
DEFAULT_EWMA_WEIGHTS: Tuple[float, ...] = (1.0, 0.7, 0.5, 0.3)

# Hard bounds on the dynamic per-item buffer derived from historical variance.
MIN_BUFFER = 0.05   # 5% floor keeps flat-selling items from hitting zero cushion.
MAX_BUFFER = 0.30   # 30% ceiling avoids runaway over-ordering on noisy items.
FALLBACK_BUFFER = 0.10


@dataclass
class ItemForecast:
    name: str
    forecast_portions: float      # weighted mean × (1 + buffer) × covers_multiplier
    baseline_mean: float          # EWMA mean before buffer / covers
    buffer: float                 # fraction actually applied (0.05..0.30)
    covers_multiplier: float      # 1.0 when no reservation data available
    samples: int                  # non-zero weeks in the 4-week window


def ewma_mean(samples: Sequence[Optional[float]], weights: Sequence[float] = DEFAULT_EWMA_WEIGHTS) -> float:
    """Return the exponentially-weighted mean of ``samples`` (week-1 first).

    ``samples[i] is None`` means we have no data for that week; it drops out of
    both numerator and denominator. If every sample is missing we return 0.
    """
    if not samples:
        return 0.0
    total = 0.0
    wsum = 0.0
    for i, v in enumerate(samples):
        if v is None:
            continue
        w = weights[i] if i < len(weights) else weights[-1]
        total += float(v) * w
        wsum += w
    if wsum <= 0:
        return 0.0
    return total / wsum


def coefficient_of_variation(samples: Sequence[Optional[float]]) -> Optional[float]:
    """Plain CV = stddev / mean over the non-missing samples. ``None`` if
    there aren't at least 2 observations or the mean is 0."""
    pts = [float(x) for x in samples if x is not None]
    n = len(pts)
    if n < 2:
        return None
    mean = sum(pts) / n
    if mean <= 0:
        return None
    var = sum((p - mean) ** 2 for p in pts) / n
    stddev = var ** 0.5
    return stddev / mean


def dynamic_buffer(samples: Sequence[Optional[float]]) -> float:
    """Per-item buffer derived from historical sales variance.

    Items with only 0–1 data points fall back to the static 10%.
    Everything else is clamped to the [5%, 30%] band defined above.
    """
    cv = coefficient_of_variation(samples)
    if cv is None:
        return FALLBACK_BUFFER
    return max(MIN_BUFFER, min(MAX_BUFFER, cv))


def forecast_item(
    samples: Sequence[Optional[float]],
    covers_multiplier: float = 1.0,
    weights: Sequence[float] = DEFAULT_EWMA_WEIGHTS,
) -> ItemForecast:
    """Compose EWMA mean + dynamic buffer + covers overlay in one call."""
    mean = ewma_mean(samples, weights)
    buf = dynamic_buffer(samples)
    mult = max(0.5, min(2.0, float(covers_multiplier or 1.0)))
    forecast = mean * (1.0 + buf) * mult
    non_zero = sum(1 for s in samples if s)
    return ItemForecast(
        name="",
        forecast_portions=round(forecast, 3),
        baseline_mean=round(mean, 3),
        buffer=round(buf, 3),
        covers_multiplier=round(mult, 3),
        samples=non_zero,
    )


def covers_multiplier(
    expected_covers: Optional[float],
    baseline_covers: Optional[float],
) -> float:
    """Return ``expected / baseline`` clamped to a sane [0.5, 2.0] range.

    Returns 1.0 when either side is missing or non-positive — in that case the
    forecast falls back to pure sales history and the manager isn't surprised
    by a phantom 3× Friday spike.
    """
    if not expected_covers or not baseline_covers:
        return 1.0
    try:
        exp = float(expected_covers)
        base = float(baseline_covers)
    except (TypeError, ValueError):
        return 1.0
    if exp <= 0 or base <= 0:
        return 1.0
    ratio = exp / base
    return max(0.5, min(2.0, ratio))


# ---------------------------------------------------------------------------
# EatNow covers lookup — per-tenant, scoped to the target dates.
# ---------------------------------------------------------------------------


def _sum_covers(queryset) -> int:
    """Sum ``group_size`` defensively (old rows may have NULL)."""
    return sum(int(r.group_size or 0) for r in queryset)


def eatnow_covers_for_dates(restaurant, target_dates: Iterable[date]) -> Tuple[float, float]:
    """Return ``(expected_total, baseline_total)`` across ``target_dates``.

    * expected = sum of ``group_size`` on the target dates themselves.
    * baseline = sum over the same weekdays for the 4 preceding weeks,
      divided by 4 (so it's directly comparable to a single-week total).

    Returns ``(0, 0)`` if the tenant has no EatNow data — the caller then
    treats the multiplier as 1.0.
    """
    from accounts.models import EatNowReservation

    dates = list(target_dates)
    if not dates:
        return 0.0, 0.0

    expected_qs = EatNowReservation.objects.filter(
        restaurant=restaurant,
        is_deleted=False,
        reservation_date__in=dates,
    )
    expected_total = _sum_covers(expected_qs)

    # Baseline: same weekday for weeks -1..-4, summed then averaged to one
    # week equivalent so it's comparable to the expected total.
    baseline_dates = [d - timedelta(weeks=w) for d in dates for w in range(1, 5)]
    if not baseline_dates:
        return float(expected_total), 0.0
    baseline_qs = EatNowReservation.objects.filter(
        restaurant=restaurant,
        is_deleted=False,
        reservation_date__in=baseline_dates,
    )
    baseline_total_raw = _sum_covers(baseline_qs)
    baseline_total = baseline_total_raw / 4.0

    return float(expected_total), float(baseline_total)


# ---------------------------------------------------------------------------
# Pack-rounding / lead-time helpers (Phase 1 purchasing math).
# ---------------------------------------------------------------------------


def _dec(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def suggest_order_qty(
    gap: float | Decimal,
    pack_size: Optional[float | Decimal],
    min_order_qty: Optional[float | Decimal],
) -> Decimal:
    """Round ``gap`` up to the next full pack and enforce supplier minimums.

    * ``gap <= 0`` → 0 (nothing to order).
    * ``pack_size`` given → ceil(gap / pack_size) × pack_size.
    * ``min_order_qty`` given → order = max(order, min_order_qty).
    """
    gap_d = _dec(gap)
    if gap_d <= 0:
        return Decimal("0")
    pack_d = _dec(pack_size) if pack_size else Decimal("0")
    min_d = _dec(min_order_qty) if min_order_qty else Decimal("0")

    if pack_d > 0:
        packs = (gap_d / pack_d).to_integral_value(rounding=ROUND_CEILING)
        order = packs * pack_d
    else:
        order = gap_d

    if min_d > 0 and order < min_d:
        order = min_d
    return order.quantize(Decimal("0.001"))


def order_by_date(target_start: date, lead_time_days: Optional[int]) -> date:
    days = int(lead_time_days or 0)
    if days <= 0:
        return target_start
    return target_start - timedelta(days=days)


# ---------------------------------------------------------------------------
# Utility to reshape ``pos_sales_by_date`` into per-item week samples.
# ---------------------------------------------------------------------------


def samples_by_item(
    pos_sales_by_date: Dict[date, Dict[str, float]],
    target_date: date,
    weeks: int = 4,
) -> Dict[str, List[Optional[float]]]:
    """Build a ``{item_name: [w1, w2, w3, w4]}`` table, preserving
    ``None`` for weeks where the POS returned no data (e.g. the shop was
    closed). Callers use this directly with :func:`forecast_item`.
    """
    names: set[str] = set()
    week_dates = [target_date - timedelta(weeks=w) for w in range(1, weeks + 1)]
    for d in week_dates:
        day = pos_sales_by_date.get(d) or {}
        names.update(day.keys())

    out: Dict[str, List[Optional[float]]] = {}
    for name in names:
        if not name:
            continue
        series: List[Optional[float]] = []
        for d in week_dates:
            day = pos_sales_by_date.get(d)
            if day is None:
                series.append(None)
            else:
                series.append(float(day.get(name, 0) or 0))
        out[name] = series
    return out
