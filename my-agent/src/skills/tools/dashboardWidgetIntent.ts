/**
 * Resolve natural-language dashboard widget requests to dashboard_widgets tool args.
 * Mirrors backend `widget_alias_resolver.py` for the most common manager phrases.
 */

const WIDGET_REQUEST_RE =
    /\b(create|add|make|put|show|display|cr[eé]e|cr[eé]er|ajoute|ajouter|zid|agrega|add)\b[\s\S]{0,80}\bwidget\b/i;

const TITLE_FROM_FOR_RE =
    /\bwidget\b\s*(?:for|pour|de|about|called|named|titled|«|"|')\s*([^"'\n.]+)/i;

/** Built-in lane aliases → widget id (subset of widget_alias_resolver.py). */
const PHRASE_TO_WIDGET: Array<{ match: RegExp; widget: string; label: string }> = [
    {
        match: /\b(team\s+leave|leave\s+request|leave\s+requests|time\s+off|holiday\s+request|cong[eé]|demande(s)?\s+de\s+cong[eé]|اجاز)/i,
        widget: "team_travel",
        label: "Team Travel",
    },
    {
        match: /\b(team\s+travel(l)?ing|travel(l)?ing(\s+needs)?|travel\s+request|voyage|deplacement|d[eé]placement)/i,
        widget: "team_travel",
        label: "Team Travel",
    },
    { match: /\b(purchases?|procurement|achats?|po\b|bons?\s+de\s+commande)/i, widget: "purchase_orders", label: "Purchases" },
    { match: /\b(human\s+resources?|\bhr\b|\brh\b|ressources?\s+humaines?)/i, widget: "human_resources", label: "HR" },
    { match: /\b(finance|invoices?|factures?|billing|payroll|paie)/i, widget: "finance", label: "Finance" },
    { match: /\b(maintenance|repairs?|entretien|صيانة)/i, widget: "maintenance", label: "Maintenance" },
    { match: /\b(urgent|urgences?|top\s+urgent)/i, widget: "urgent_top", label: "Urgent" },
    { match: /\b(staff\s+inbox|inbox|staff\s+requests?|demandes?\s+du\s+personnel)/i, widget: "staff_inbox", label: "Staff inbox" },
    { match: /\b(meetings?|calendar|reminders?|r[eé]unions?|calendrier)/i, widget: "meetings_reminders", label: "Meetings & reminders" },
    { match: /\b(live\s+attendance|who\s+is\s+here|attendance\s+widget)/i, widget: "live_attendance", label: "Live attendance" },
    { match: /\b(clock[\s-]?in|clock\s+ins?|pointage|attendance|حضور)/i, widget: "clock_ins", label: "Clock-ins" },
    { match: /\b(incidents?)/i, widget: "incidents", label: "Incidents" },
    { match: /\b(inventory|stock|deliveries|inventaire|مخزون)/i, widget: "inventory_delivery", label: "Inventory" },
    { match: /\b(operations?\s+tasks?|\bops\b\s+tasks?)/i, widget: "operations_tasks", label: "Operations tasks" },
    { match: /\b(tasks?\s+and\s+demands?|\btasks\b|\btodo\b|t[aâ]ches?)/i, widget: "tasks_demands", label: "Tasks & demands" },
    { match: /\b(misc|miscellaneous|other|divers)/i, widget: "miscellaneous", label: "Miscellaneous" },
];

export type DashboardWidgetIntent =
    | { action: "add"; widgets: string[]; label: string; sourceText: string }
    | { action: "create_custom"; title: string; sourceText: string };

export function isDashboardWidgetRequest(text: string): boolean {
    const t = text.trim();
    if (!t) return false;
    if (WIDGET_REQUEST_RE.test(t)) return true;
    if (/\bwidget\b/i.test(t) && /\b(for|pour|de|team leave|leave request)\b/i.test(t)) return true;
    return false;
}

export function extractWidgetTitlePhrase(text: string): string {
    const t = text.trim();
    const forMatch = t.match(TITLE_FROM_FOR_RE);
    if (forMatch?.[1]) {
        return forMatch[1].trim().replace(/\s+widget\s*$/i, "").trim();
    }
    const stripped = t
        .replace(WIDGET_REQUEST_RE, "")
        .replace(/^\s*(a|an|the|un|une|le|la|my|on|to|for|pour)\s+/i, "")
        .trim();
    return stripped || t;
}

export function resolveDashboardWidgetIntent(text: string): DashboardWidgetIntent | null {
    if (!isDashboardWidgetRequest(text)) return null;

    const phrase = extractWidgetTitlePhrase(text);
    for (const { match, widget, label } of PHRASE_TO_WIDGET) {
        if (match.test(phrase) || match.test(text)) {
            return { action: "add", widgets: [widget], label, sourceText: text };
        }
    }

    return { action: "create_custom", title: phrase.slice(0, 255), sourceText: text };
}
