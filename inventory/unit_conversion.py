"""
Unit conversion for prep list / inventory math.

We convert a recipe-side quantity (e.g. ``250 g`` per portion) into the
inventory item's native unit (e.g. ``kg``) so that "need vs in_stock" math and
purchase-order line items stay consistent.

Canonical bases:

* mass   → grams (GRAM)
* volume → millilitres (ML)
* count  → units (UNIT)

``BOX`` can be converted to ``UNIT`` (and vice versa) when a ``pack_size`` is
provided. ``BAG`` is treated as opaque: we keep the quantity untouched if the
recipe already speaks bags, otherwise we refuse to convert (callers get the
original value back so the prep list still renders something useful).

All functions accept ``int``, ``float`` or :class:`decimal.Decimal` and return a
:class:`decimal.Decimal` so caller arithmetic stays exact.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

Number = Decimal | float | int

# Mass
GRAM = "GRAM"
KG = "KG"
# Volume
ML = "ML"
LITER = "LITER"
# Count
UNIT = "UNIT"
BOX = "BOX"
BAG = "BAG"

_MASS = {GRAM, KG}
_VOLUME = {ML, LITER}
_COUNT = {UNIT, BOX, BAG}

# Aliases frequently typed on recipes.
_ALIASES = {
    "G": GRAM,
    "GR": GRAM,
    "GRAMS": GRAM,
    "GRAM": GRAM,
    "KILO": KG,
    "KILOS": KG,
    "KG": KG,
    "KGS": KG,
    "KILOGRAM": KG,
    "KILOGRAMS": KG,
    "ML": ML,
    "MILLILITER": ML,
    "MILLILITRE": ML,
    "MILLILITERS": ML,
    "MILLILITRES": ML,
    "L": LITER,
    "LT": LITER,
    "LITER": LITER,
    "LITRE": LITER,
    "LITERS": LITER,
    "LITRES": LITER,
    "UNIT": UNIT,
    "UNITS": UNIT,
    "PC": UNIT,
    "PCS": UNIT,
    "PIECE": UNIT,
    "PIECES": UNIT,
    "EA": UNIT,
    "EACH": UNIT,
    "BOX": BOX,
    "BOXES": BOX,
    "BAG": BAG,
    "BAGS": BAG,
}


def normalize_unit(unit: Optional[str]) -> str:
    """Normalise a free-text unit into one of the canonical codes.

    Unknown values are returned upper-cased and stripped (and will compare
    equal only to themselves), which keeps conversion honest: if both the
    recipe and the inventory item use the same non-canonical code we'll still
    pass the quantity through untouched.
    """
    if not unit:
        return ""
    key = str(unit).strip().upper()
    return _ALIASES.get(key, key)


def _as_decimal(value: Number) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _family(unit: str) -> str:
    if unit in _MASS:
        return "mass"
    if unit in _VOLUME:
        return "volume"
    if unit in _COUNT:
        return "count"
    return "other"


def _to_base(qty: Decimal, unit: str, pack_size: Optional[Decimal]) -> Tuple[Decimal, str]:
    """Convert *qty* expressed in *unit* to the canonical base of its family."""
    if unit == KG:
        return qty * Decimal(1000), GRAM
    if unit == LITER:
        return qty * Decimal(1000), ML
    if unit == BOX and pack_size and pack_size > 0:
        return qty * pack_size, UNIT
    return qty, unit


def _from_base(qty: Decimal, base_unit: str, to_unit: str, pack_size: Optional[Decimal]) -> Optional[Decimal]:
    if base_unit == to_unit:
        return qty
    if base_unit == GRAM and to_unit == KG:
        return qty / Decimal(1000)
    if base_unit == ML and to_unit == LITER:
        return qty / Decimal(1000)
    if base_unit == UNIT and to_unit == BOX and pack_size and pack_size > 0:
        return qty / pack_size
    return None


def convert(
    qty: Number,
    from_unit: Optional[str],
    to_unit: Optional[str],
    pack_size: Optional[Number] = None,
) -> Tuple[Decimal, bool]:
    """Convert *qty* from *from_unit* to *to_unit*.

    Returns ``(converted_qty, ok)``. ``ok`` is ``False`` when the units belong
    to different families (e.g. mass → count) or when BOX↔UNIT is requested
    without a ``pack_size``. In that case ``converted_qty`` is the original
    quantity (so prep lists still render a number instead of blank).
    """
    q = _as_decimal(qty)
    f = normalize_unit(from_unit)
    t = normalize_unit(to_unit)
    pack = _as_decimal(pack_size) if pack_size not in (None, "") else None
    if pack is not None and pack <= 0:
        pack = None

    # No units on either side — pass through.
    if not f and not t:
        return q, True
    if not f or not t:
        return q, True  # caller chose to pass recipe-native or inventory-native

    if f == t:
        return q, True

    fam_f = _family(f)
    fam_t = _family(t)
    if fam_f != fam_t or fam_f == "other":
        return q, False

    base_qty, base_unit = _to_base(q, f, pack)
    out = _from_base(base_qty, base_unit, t, pack)
    if out is None:
        return q, False
    return out, True


def to_inventory_unit(
    qty: Number,
    recipe_unit: Optional[str],
    inventory_unit: Optional[str],
    pack_size: Optional[Number] = None,
) -> Tuple[Decimal, bool]:
    """Thin wrapper for the common direction used by the prep-list engine."""
    return convert(qty, recipe_unit, inventory_unit, pack_size)
