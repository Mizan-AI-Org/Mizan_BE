"""
Recipe / BOM food-cost helpers for manager copilot.

Uses menu.RecipeIngredient + Ingredient.cost_per_unit (same BOM as prep-list).
Unit mismatches are treated as same-unit for MVP (no conversion); callers
should treat high food_cost_pct as a signal to review recipe data.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db.models import Prefetch

from .models import MenuItem, Recipe, RecipeIngredient

TWOPLACES = Decimal("0.01")


def _q(value: Decimal | float | int | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def portion_cost_for_recipe(recipe: Recipe) -> Decimal:
    total = Decimal("0")
    for ri in recipe.ingredients.all():
        cost = ri.ingredient.cost_per_unit or Decimal("0")
        qty = ri.quantity or Decimal("0")
        total += Decimal(str(qty)) * Decimal(str(cost))
    return _q(total)


def food_cost_row(menu_item: MenuItem, recipe: Recipe | None = None) -> dict[str, Any]:
    recipe = recipe or getattr(menu_item, "recipe", None)
    price = _q(menu_item.price)
    if not recipe:
        return {
            "menu_item_id": str(menu_item.id),
            "name": menu_item.name,
            "price": float(price),
            "portion_cost": None,
            "margin": None,
            "food_cost_pct": None,
            "has_recipe": False,
            "ingredient_lines": 0,
        }

    portion = portion_cost_for_recipe(recipe)
    margin = _q(price - portion) if price else None
    pct = None
    if price > 0:
        pct = float((portion / price * Decimal("100")).quantize(TWOPLACES, rounding=ROUND_HALF_UP))

    return {
        "menu_item_id": str(menu_item.id),
        "name": menu_item.name,
        "price": float(price),
        "portion_cost": float(portion),
        "margin": float(margin) if margin is not None else None,
        "food_cost_pct": pct,
        "has_recipe": True,
        "ingredient_lines": recipe.ingredients.count()
        if hasattr(recipe, "ingredients")
        else 0,
    }


def compute_food_cost_report(restaurant, *, limit: int = 25, sort: str = "food_cost_pct") -> dict[str, Any]:
    """
    Return menu items with recipes, ranked by food-cost % (highest first)
    or by margin (lowest first when sort=margin).
    """
    qs = (
        MenuItem.objects.filter(restaurant=restaurant, is_active=True)
        .select_related("recipe", "category")
        .prefetch_related(
            Prefetch(
                "recipe__ingredients",
                queryset=RecipeIngredient.objects.select_related("ingredient"),
            )
        )
        .order_by("name")
    )

    rows: list[dict[str, Any]] = []
    missing_recipe = 0
    for item in qs:
        recipe = getattr(item, "recipe", None)
        if not recipe:
            missing_recipe += 1
            continue
        row = food_cost_row(item, recipe)
        if row["ingredient_lines"] == 0:
            continue
        rows.append(row)

    if sort == "margin":
        rows.sort(key=lambda r: (r["margin"] is None, r["margin"] if r["margin"] is not None else 0))
    else:
        rows.sort(
            key=lambda r: (r["food_cost_pct"] is None, -(r["food_cost_pct"] or 0)),
        )

    capped = rows[: max(1, min(int(limit or 25), 100))]
    avg_pct = None
    priced = [r for r in rows if r["food_cost_pct"] is not None]
    if priced:
        avg_pct = round(sum(r["food_cost_pct"] for r in priced) / len(priced), 2)

    return {
        "restaurant_id": str(restaurant.id),
        "count": len(capped),
        "total_with_recipes": len(rows),
        "missing_recipe_count": missing_recipe,
        "avg_food_cost_pct": avg_pct,
        "items": capped,
        "sort": sort,
    }
