/**
 * Resolve natural-language dashboard widget requests to dashboard_widgets tool args.
 * Mirrors backend `widget_alias_resolver.py` for the most common manager phrases.
 */

import { stripSystemContextBlocks } from "../../utils/stripSystemContext";

const WIDGET_REQUEST_RE =
    /\b(create|add|make|put|show|display|cr[eé]e|cr[eé]er|ajoute|ajouter|zid|agrega|add)\b[\s\S]{0,80}\bwidget\b/i;

const TITLE_FROM_FOR_RE =
    /\bwidget\b\s*(?:for|pour|de|about|called|named|titled|to\s+handle|to\s+track|to\s+manage|«|"|')\s*:?\s*([^"'\n.]+)/i;

/**
 * Manager named a specific custom tile (topic after "widget"), not a lane alias
 * like "create a Purchases widget".
 *
 * Examples:
 *   "create a widget called Gitex Marrakesh"
 *   "create a new widget for next week staff retreat in Bali"
 *   "Create a new widget to handle vehicle petrol expenses"
 */
const EXPLICIT_CUSTOM_WIDGET_RE =
    /\bwidget\b[\s\S]{0,40}\b(called|named|titled|for|pour|about|to\s+handle|to\s+track|to\s+manage)\b/i;

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
    {
        match: /\b(team\s+retreat|retreats?|offsite|team\s+offsite)/i,
        widget: "team_travel",
        label: "Team Travel",
    },
    {
        match: /\b(team\s+medical(\s+service(s)?)?|medical\s+service(s)?|occupational\s+health|health\s+service|clinic\s+visit|doctor\s+appointment|visite\s+m[eé]dicale|service\s+m[eé]dical)/i,
        widget: "team_medical_service",
        label: "Team Medical Service",
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

/** Strip language-mirror / system blocks before intent or title parsing. */
export function sanitizeWidgetUserText(text: string): string {
    return stripSystemContextBlocks(text || "");
}

export function isExplicitCustomWidgetRequest(text: string): boolean {
    return EXPLICIT_CUSTOM_WIDGET_RE.test(sanitizeWidgetUserText(text).trim());
}

export function isDashboardWidgetRequest(text: string): boolean {
    const t = sanitizeWidgetUserText(text).trim();
    if (!t) return false;
    if (WIDGET_REQUEST_RE.test(t)) return true;
    if (/\bwidget\b/i.test(t) && /\b(for|pour|de|team leave|leave request)\b/i.test(t)) return true;
    return false;
}

function cleanWidgetTitle(raw: string): string {
    let t = sanitizeWidgetUserText(raw)
        .trim()
        .replace(/^:\s*/, "")
        .replace(/\s+widget\s*$/i, "")
        .trim();
    // Drop leftover directive crumbs if any slipped through.
    t = t.replace(/^\[(?:REPLY LANGUAGE|LANGUAGE DETECTED)[^\]]*\]\s*/i, "").trim();
    if (!t) return "";
    return t.charAt(0).toUpperCase() + t.slice(1);
}

export function extractWidgetTitlePhrase(text: string): string {
    const t = sanitizeWidgetUserText(text).trim();
    const forMatch = t.match(TITLE_FROM_FOR_RE);
    if (forMatch?.[1]) {
        return cleanWidgetTitle(forMatch[1]).slice(0, 255);
    }
    let stripped = t
        .replace(
            /^\s*(create|add|make|put|show|display|cr[eé]e|cr[eé]er|ajoute|ajouter|zid|agrega)\s+(?:a|an|the|un|une|my|le|la|to|for|pour)?\s*/i,
            "",
        )
        .replace(/^(?:new\s+)?widget\s+/i, "")
        .replace(/\s+widget\s*$/i, "")
        .trim();
    return cleanWidgetTitle(stripped || t).slice(0, 255);
}

export function resolveOperationalWidgetFromPhrase(text: string): DashboardWidgetIntent | null {
    const t = sanitizeWidgetUserText(text).trim();
    if (!t || isExplicitCustomWidgetRequest(t)) return null;
    const phrase = extractWidgetTitlePhrase(t);
    for (const { match, widget, label } of PHRASE_TO_WIDGET) {
        if (match.test(phrase) || match.test(t)) {
            return { action: "add", widgets: [widget], label, sourceText: t };
        }
    }
    return null;
}

export function resolveDashboardWidgetIntent(text: string): DashboardWidgetIntent | null {
    const cleaned = sanitizeWidgetUserText(text);
    if (!isDashboardWidgetRequest(cleaned)) return null;

    const phrase = extractWidgetTitlePhrase(cleaned);
    const explicitCustom = isExplicitCustomWidgetRequest(cleaned);

    if (!explicitCustom) {
        for (const { match, widget, label } of PHRASE_TO_WIDGET) {
            if (match.test(phrase) || match.test(cleaned)) {
                return { action: "add", widgets: [widget], label, sourceText: cleaned };
            }
        }
    } else {
        // Exact/short lane titles after "widget for …" still add the built-in
        // (e.g. "widget for leave requests"). Specific topics stay custom.
        const exact = resolveExactOperationalTitle(phrase);
        if (exact) {
            return { ...exact, sourceText: cleaned };
        }
    }

    const title = phrase.slice(0, 255);
    if (!title || /^\[(?:REPLY LANGUAGE|LANGUAGE DETECTED)/i.test(title)) {
        return null;
    }

    return { action: "create_custom", title, sourceText: cleaned };
}

/** Whole-phrase alias only — no substring "retreat" inside a longer title. */
function resolveExactOperationalTitle(
    phrase: string,
): { action: "add"; widgets: string[]; label: string } | null {
    const key = phrase
        .toLowerCase()
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-z0-9\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    if (!key) return null;

    const EXACT: Record<string, { widget: string; label: string }> = {
        "leave request": { widget: "team_travel", label: "Team Travel" },
        "leave requests": { widget: "team_travel", label: "Team Travel" },
        "team leave": { widget: "team_travel", label: "Team Travel" },
        "team leave request": { widget: "team_travel", label: "Team Travel" },
        "time off": { widget: "team_travel", label: "Team Travel" },
        "team travel": { widget: "team_travel", label: "Team Travel" },
        retreat: { widget: "team_travel", label: "Team Travel" },
        retreats: { widget: "team_travel", label: "Team Travel" },
        "team retreat": { widget: "team_travel", label: "Team Travel" },
        purchases: { widget: "purchase_orders", label: "Purchases" },
        purchase: { widget: "purchase_orders", label: "Purchases" },
        "human resources": { widget: "human_resources", label: "HR" },
        hr: { widget: "human_resources", label: "HR" },
        finance: { widget: "finance", label: "Finance" },
        maintenance: { widget: "maintenance", label: "Maintenance" },
        incidents: { widget: "incidents", label: "Incidents" },
        "staff inbox": { widget: "staff_inbox", label: "Staff inbox" },
        inbox: { widget: "staff_inbox", label: "Staff inbox" },
    };
    const hit = EXACT[key];
    if (!hit) return null;
    return { action: "add", widgets: [hit.widget], label: hit.label };
}
