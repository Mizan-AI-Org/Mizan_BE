/** Regex for the tenant-context block injected by TenantContextPreprocessor. */
export const PERSISTENT_CONTEXT_BLOCK_RE =
    /\[SYSTEM: PERSISTENT CONTEXT\][\s\S]*?AGENT_IDENTITY_VERIFIED:\s*TRUE/gi;

export const PARTIAL_CONTEXT_BLOCK_RE =
    /\[SYSTEM: PARTIAL CONTEXT\][\s\S]*?(?=\n\n\[|$)/gi;

export const SYSTEM_CONTEXT_MARKERS = [
    "[SYSTEM: PERSISTENT CONTEXT]",
    "[SYSTEM: PARTIAL CONTEXT]",
] as const;

export function containsSystemContextBlock(text: string | null | undefined): boolean {
    if (!text) return false;
    return SYSTEM_CONTEXT_MARKERS.some((marker) => text.includes(marker));
}

/** Remove injected system context from text shown to humans (chat UI / replies). */
export function stripSystemContextBlocks(text: string): string {
    return text
        .replace(PERSISTENT_CONTEXT_BLOCK_RE, "")
        .replace(PARTIAL_CONTEXT_BLOCK_RE, "")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
}
