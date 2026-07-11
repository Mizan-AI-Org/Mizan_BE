/**
 * Map Lua delivery channel → message audience.
 *
 * WhatsApp (and voice/SMS) = staff-facing replies.
 * LuaPop / web dashboard embed = manager/admin-facing replies.
 */
export type MessageAudience = "staff" | "manager";

export function resolveMessageAudience(channel: string | undefined | null): MessageAudience {
    const ch = String(channel || "")
        .toLowerCase()
        .trim();

    if (!ch || ch === "whatsapp" || ch === "voice" || ch === "sms" || ch === "phone") {
        return "staff";
    }

    if (
        ch === "web" ||
        ch === "webchat" ||
        ch === "luapop" ||
        ch === "pop" ||
        ch === "dashboard" ||
        ch.includes("web")
    ) {
        return "manager";
    }

    // Unknown channel — default to manager (safer than leaking dashboard jargon on WhatsApp).
    return "manager";
}

export function resolveMessageAudienceFromContext(context?: {
    channel?: string | { type?: string; name?: string };
}): MessageAudience {
    const raw =
        (typeof context?.channel === "object"
            ? context.channel.type || context.channel.name
            : context?.channel) || "whatsapp";
    return resolveMessageAudience(String(raw));
}

export function audienceContextLine(audience: MessageAudience): string {
    if (audience === "staff") {
        return [
            "Delivery channel: WhatsApp (STAFF audience).",
            "Reply warm, short, and reassuring in the user's language.",
            "No dashboard, widget, inbox, lane, triage, or command-centre jargon.",
            "Never tell them to open the app or refresh a dashboard widget.",
        ].join(" ");
    }
    return [
        "Delivery channel: LuaPop / dashboard (MANAGER or ADMIN audience).",
        "Reply operational and concise.",
        "You MAY reference dashboard widgets, inbox lanes, assignees, WhatsApp delivery status, and automatic follow-ups.",
    ].join(" ");
}

/** Patterns that must not appear in staff-facing (WhatsApp) replies. */
export const MANAGER_JARGON_PATTERNS: RegExp[] = [
    /\bdashboard widget(s)?\b/gi,
    /\brefresh your dashboard\b/gi,
    /\bcommand centre\b/gi,
    /\bcommand center\b/gi,
    /\binbox lane\b/gi,
    /\bwidget lane\b/gi,
    /\bcategory owner\b/gi,
    /\bpropri[eé]taire de cat[eé]gorie\b/gi,
    /\bauto-?pin(ned)?\b/gi,
    /\bpinned to (?:your )?dashboard\b/gi,
    /\bstaff inbox\b/gi,
    /\bTasks & Demands\b/gi,
    /\bPurchase Orders widget\b/gi,
    /\bReported Incidents board\b/gi,
];
