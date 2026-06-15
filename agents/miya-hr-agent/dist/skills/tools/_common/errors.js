/**
 * Structured tool errors that Miya must translate into the user's language
 * and rewrite in a friendly, non-technical way before showing to the user.
 *
 * Every error carries:
 *   - code: stable machine code (for routing + tests)
 *   - message: a short neutral English description (NOT meant to be shown verbatim)
 *   - miya_directive: explicit instructions for how Miya should rewrite it
 *
 * Never surface the `.message` field to the user raw. Miya's persona forbids
 * leaking raw technical errors — she must translate into the user's language.
 */
export function noContextError(opts) {
    return {
        status: "error",
        code: "NO_TENANT_CONTEXT",
        message: "No workspace is linked to this conversation yet, so I can't read tenant-scoped data.",
        miya_directive: "Rewrite this in the user's language (match their last message — English, French, Modern Standard Arabic, or Moroccan Darija). Apologise briefly, say you don't yet have the workspace linked to this chat, and offer the fix: either open Miya from the Mizan dashboard, or (if they are staff) message from the WhatsApp number linked to their account. NEVER mention 'Restaurant context required', 'tenant', 'token', 'JWT', or any technical jargon. NEVER mention a specific tool name.",
        fallback_suggestion: opts?.hint,
    };
}
export function notAuthorizedError() {
    return {
        status: "error",
        code: "NOT_AUTHORIZED",
        message: "The current session is not authorised for this action.",
        miya_directive: "Rewrite in the user's language. Explain kindly that their session doesn't grant permission for this, and suggest they sign in again or ask the workspace owner. No technical jargon.",
    };
}
export function upstreamError(detail) {
    return {
        status: "error",
        code: "UPSTREAM",
        message: detail || "Backend call failed.",
        miya_directive: "Apologise briefly in the user's language. Say something on our side is temporarily unavailable and to try again in a moment. Do NOT show the raw technical detail.",
    };
}
export function validationError(detail) {
    return {
        status: "error",
        code: "VALIDATION",
        message: detail,
        miya_directive: "Translate the detail into plain, friendly language in the user's language and ask for the missing/invalid piece. No jargon.",
    };
}
export function notFoundError(what) {
    return {
        status: "error",
        code: "NOT_FOUND",
        message: `${what} not found.`,
        miya_directive: "Tell the user in their language that you couldn't find what they asked for. Offer to broaden the search or ask for a clarifying detail.",
    };
}
