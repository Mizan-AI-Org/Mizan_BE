"""AUTHORIZE — pre-execute permission and establishment checks."""
from __future__ import annotations

from miya.services.intelligence.copilot.understand import is_mutation_intent
from miya.services.intelligence.planning.types import ClassifiedIntent, IntentClass
from miya.services.ops.context import OpsContext, require_permission
from miya.services.ops.result import OpsResult

# Map intents → RBAC action ids (None = no extra gate beyond ops handler)
_INTENT_PERMISSION: dict[IntentClass, str | None] = {
    IntentClass.COMPLETE: "manage_widgets",
    IntentClass.ASSIGN: "manage_widgets",
    IntentClass.CREATE: "manage_widgets",
    IntentClass.APPROVE: "manage_widgets",
    IntentClass.REJECT: "manage_widgets",
    IntentClass.ROUTE: "manage_widgets",
    IntentClass.REMIND: None,
    IntentClass.SCHEDULE: None,
    IntentClass.DELETE: "manage_widgets",
    IntentClass.UPLOAD: "manage_widgets",
    IntentClass.UPDATE: "manage_widgets",
}


def authorize_mutation(
    classified: ClassifiedIntent,
    ctx: OpsContext,
) -> OpsResult | None:
    """
    Pre-flight AUTHORIZE for mutation intents.
    Returns OpsResult deny/clarify, or None if OK to proceed.
    """
    if not is_mutation_intent(classified):
        return None
    action_id = _INTENT_PERMISSION.get(classified.intent)
    if not action_id:
        return None
    if classified.intent in (IntentClass.APPROVE, IntentClass.REJECT):
        action_id = "manage_widgets"
    return require_permission(ctx, action_id)
