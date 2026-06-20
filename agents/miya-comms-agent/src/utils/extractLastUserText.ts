import type { ChatMessage } from "lua-cli";

/** Single message → visible user text (text, body, button label, etc.). */
export function extractMessageText(msg: ChatMessage | Record<string, unknown>): string {
    const m = msg as unknown as Record<string, unknown>;
    const type = String(m.type || "").toLowerCase();

    if (typeof m.text === "string" && m.text.trim()) {
        return m.text.trim();
    }

    if (m.text && typeof m.text === "object" && !Array.isArray(m.text)) {
        const nested = m.text as Record<string, unknown>;
        if (typeof nested.body === "string" && nested.body.trim()) {
            return nested.body.trim();
        }
    }

    if (typeof m.body === "string" && m.body.trim()) {
        return m.body.trim();
    }

    if (typeof m.content === "string" && m.content.trim()) {
        return m.content.trim();
    }

    if (
        type === "button" ||
        type === "interactive" ||
        type === "quick_reply" ||
        type === "postback"
    ) {
        for (const key of ["button_text", "title", "label", "payload", "id"]) {
            const v = m[key];
            if (typeof v === "string" && v.trim()) {
                return v.trim();
            }
        }
    }

    return "";
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
