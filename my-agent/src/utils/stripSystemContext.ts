/** Regex for the tenant-context block injected by TenantContextPreprocessor. */
export const PERSISTENT_CONTEXT_BLOCK_RE =
    /\[SYSTEM: PERSISTENT CONTEXT\][\s\S]*?AGENT_IDENTITY_VERIFIED:\s*TRUE/gi;

export const PARTIAL_CONTEXT_BLOCK_RE =
    /\[SYSTEM: PARTIAL CONTEXT\][\s\S]*?(?=\n\n\[|$)/gi;

/** LanguageMirror / Space language-enforcement blocks prefixed onto user turns. */
export const LANGUAGE_DIRECTIVE_BLOCK_RE =
    /\[(?:REPLY LANGUAGE[^\]]*|LANGUAGE DETECTED)\][^\n]*(?:\n(?!\n)[^\n]*)*(?:\n\n)?/gi;

export const SYSTEM_CONTEXT_MARKERS = [
    "[SYSTEM: PERSISTENT CONTEXT]",
    "[SYSTEM: PARTIAL CONTEXT]",
    "[REPLY LANGUAGE — NON-NEGOTIABLE]",
    "[REPLY LANGUAGE]",
    "[LANGUAGE DETECTED]",
] as const;

export function containsSystemContextBlock(text: string | null | undefined): boolean {
    if (!text) return false;
    return SYSTEM_CONTEXT_MARKERS.some((marker) => text.includes(marker));
}

/**
 * Remove injected system / language-mirror context from text shown to humans
 * or used as widget titles / tool source_text.
 */
export function stripSystemContextBlocks(text: string): string {
    let out = text
        .replace(PERSISTENT_CONTEXT_BLOCK_RE, "")
        .replace(PARTIAL_CONTEXT_BLOCK_RE, "")
        .replace(LANGUAGE_DIRECTIVE_BLOCK_RE, "");

    // Fallback: directive still leading the string (no clean blank-line split).
    while (/^\[(?:REPLY LANGUAGE|LANGUAGE DETECTED)/i.test(out.trim())) {
        const trimmed = out.trim();
        const idx = trimmed.indexOf("\n\n");
        if (idx < 0) {
            out = "";
            break;
        }
        out = trimmed.slice(idx + 2);
    }

    return out.replace(/\n{3,}/g, "\n\n").trim();
}
