import type { ChatMessage } from "lua-cli";

/** Strip WhatsApp quote prefix from a single line (e.g. "You: Clock in"). */
export function stripQuotedReplyPrefix(text: string): string {
    const t = text.trim();
    const m = t.match(/^(?:You|Vous|Tu)\s*:\s*(.+)$/i);
    return m ? m[1].trim() : t;
}

/** Pull embedded "You: …" lines from multi-line button-reply bodies. */
export function extractQuotedUserLines(text: string): string[] {
    const out: string[] = [];
    for (const m of text.matchAll(/(?:^|\n)(?:You|Vous|Tu)\s*:\s*([^\n]+)/gi)) {
        const line = m[1].trim();
        if (line) out.push(line);
    }
    return out;
}

/** Single message → visible user text (text, body, button label, quoted reply, etc.). */
export function extractMessageText(msg: ChatMessage | Record<string, unknown>): string {
    const m = msg as unknown as Record<string, unknown>;
    const type = String(m.type || "").toLowerCase();

    let raw = "";

    if (typeof m.text === "string" && m.text.trim()) {
        raw = m.text.trim();
    } else if (m.text && typeof m.text === "object" && !Array.isArray(m.text)) {
        const nested = m.text as Record<string, unknown>;
        if (typeof nested.body === "string" && nested.body.trim()) {
            raw = nested.body.trim();
        }
    } else if (typeof m.body === "string" && m.body.trim()) {
        raw = m.body.trim();
    } else if (typeof m.content === "string" && m.content.trim()) {
        raw = m.content.trim();
    } else if (
        type === "button" ||
        type === "interactive" ||
        type === "quick_reply" ||
        type === "postback"
    ) {
        for (const key of ["button_text", "title", "label", "payload", "id"]) {
            const v = m[key];
            if (typeof v === "string" && v.trim()) {
                raw = v.trim();
                break;
            }
        }
    }

    if (raw) {
        return stripQuotedReplyPrefix(raw);
    }

    const context = m.context as Record<string, unknown> | undefined;
    const quoted = context?.quoted_message ?? context?.referred_product;
    if (quoted && typeof quoted === "object") {
        const qt = extractMessageText(quoted as Record<string, unknown>);
        if (qt) return qt;
    }

    return "";
}

/**
 * Collect every user-visible phrase from the batch (primary text + embedded "You:" quotes
 * + WhatsApp quoted_message context). Newest-first for confirm-button recovery.
 */
export function collectUserTextsFromMessages(messages: ChatMessage[]): string[] {
    const out: string[] = [];
    for (let i = messages.length - 1; i >= 0; i--) {
        const m = messages[i] as unknown as Record<string, unknown>;
        const primary = extractMessageText(messages[i] as ChatMessage);
        if (primary) {
            out.push(primary);
            for (const q of extractQuotedUserLines(primary)) {
                out.push(q);
            }
        }
        const context = m.context as Record<string, unknown> | undefined;
        const quoted = context?.quoted_message;
        if (quoted && typeof quoted === "object") {
            const qt = extractMessageText(quoted as Record<string, unknown>);
            if (qt) out.push(qt);
        }
    }
    return out;
}

/**
 * Best-effort last user-visible text from a Lua message batch.
 * Quick-reply buttons and interactive payloads may not use type "text".
 */
export function extractLastUserText(messages: ChatMessage[]): string {
    for (let i = messages.length - 1; i >= 0; i--) {
        const text = extractMessageText(messages[i] as ChatMessage);
        if (text) return text;
    }
    return "";
}
