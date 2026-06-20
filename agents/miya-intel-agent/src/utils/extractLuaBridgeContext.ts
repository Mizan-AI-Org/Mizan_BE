/**
 * Parse Mizan tenant/user ids embedded in Lua bridge session uids, e.g.:
 *   baseAgent_...-tenant-<restaurantUuid>-user-<mizanUserUuid>-...
 *   tenant-<restaurantUuid>-user-<mizanUserUuid>
 */

const UUID =
    "[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";

export function extractRestaurantIdFromLuaBridgeId(raw: unknown): string | undefined {
    if (!raw || typeof raw !== "string") return undefined;
    const s = raw.trim();
    const embedded = s.match(new RegExp(`-tenant-(${UUID})-user-`, "i"));
    if (embedded?.[1]) return embedded[1];
    const prefix = s.match(new RegExp(`^tenant-(${UUID})-user-`, "i"));
    return prefix?.[1];
}

export function extractMizanUserIdFromLuaBridgeId(raw: unknown): string | undefined {
    if (!raw || typeof raw !== "string") return undefined;
    const m = raw.trim().match(new RegExp(`-user-(${UUID})`, "i"));
    return m?.[1];
}
