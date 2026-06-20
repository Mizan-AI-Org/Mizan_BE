import { extractMizanUserIdFromLuaBridgeId } from "./extractLuaBridgeContext";

export type LuaUserStaffIdSource = {
    uid?: string | null;
    data?: Record<string, unknown> | null;
    _luaProfile?: Record<string, unknown> | null;
};

/**
 * Resolve Mizan CustomUser id from LuaPop metadata, synced user.data, or bridge uid.
 */
export function resolveStaffIdFromLuaUser(
    user: LuaUserStaffIdSource | null | undefined,
): string {
    if (!user) return "";

    const data = user.data || {};
    const profile = user._luaProfile || {};
    const meta =
        (profile as { metadata?: unknown }).metadata &&
        typeof (profile as { metadata?: unknown }).metadata === "object"
            ? ((profile as { metadata?: Record<string, unknown> }).metadata as Record<string, unknown>)
            : {};

    const candidates: unknown[] = [
        data.mizanUserId,
        data.userId,
        meta.mizanUserId,
        meta.userId,
        meta.user_id,
    ];

    for (const raw of candidates) {
        const id = String(raw ?? "").trim();
        if (id && id.length >= 8) {
            return id;
        }
    }

    const uid = user.uid != null ? String(user.uid).trim() : "";
    if (uid) {
        const fromUid = extractMizanUserIdFromLuaBridgeId(uid);
        if (fromUid) return fromUid;
    }

    const sessionId = (profile as { sessionId?: unknown }).sessionId;
    const fromSession = extractMizanUserIdFromLuaBridgeId(sessionId);
    if (fromSession) return fromSession;

    return "";
}

export function isWebDeliveryChannel(channel: string | undefined | null): boolean {
    const ch = String(channel || "")
        .toLowerCase()
        .trim();
    if (!ch) return false;
    return (
        ch === "web" ||
        ch === "webchat" ||
        ch === "luapop" ||
        ch === "pop" ||
        ch === "dashboard" ||
        ch.includes("web")
    );
}
