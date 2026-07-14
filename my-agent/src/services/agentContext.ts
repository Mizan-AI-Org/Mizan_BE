import { User, env } from "lua-cli";
import type { UserDataInstance } from "lua-cli";
import {
    extractMizanUserIdFromLuaBridgeId,
    extractRestaurantIdFromLuaBridgeId,
} from "../utils/extractLuaBridgeContext";
import { resolveMizanUserIdFromLuaUser } from "../utils/resolveTenantForUser";

/** Mizan `CustomUser` id — UUID string from webhooks, runtimeContext, or Lua profile (never Lua's own opaque user id unless it is already a UUID). */
export function resolveMizanUserIdFromUser(user: any): string | undefined {
    if (!user) return undefined;
    return resolveMizanUserIdFromLuaUser(user as UserDataInstance);
}

export interface AgentContext {
    restaurantId: string | undefined;
    token: string | undefined;
    agentKey: string | undefined;
    userId: string | undefined;
    phone: string | undefined;
    email: string | undefined;
    userToken: string | undefined;
    sessionId: string | undefined;
}

/**
 * Robust extraction of restaurant ID, auth token, and agent key from the Lua user context.
 *
 * Checks (in priority order):
 *   1. Explicit input.restaurantId from the LLM
 *   2. User profile / metadata fields (set by LuaPop.init)
 *   3. SessionId format: "tenant-<restaurant_uuid>-user-<user_uuid>"
 *
 * For auth, prefers the stable agent key (WEBHOOK_API_KEY) over the user's JWT
 * because the JWT expires (~60 min) and the widget doesn't refresh it.
 */
export async function resolveAgentContext(inputRestaurantId?: string): Promise<AgentContext> {
    const user = await User.get();
    const userData = user ? ((user as any).data || {}) : {};
    const profile = user ? ((user as any)._luaProfile || {}) : {};
    const metadata = profile.metadata && typeof profile.metadata === "object" ? profile.metadata : {};

    // --- Restaurant ID ---
    let restaurantId: string | undefined =
        inputRestaurantId ||
        (user as any)?.restaurantId ||
        userData.restaurantId ||
        profile.restaurantId ||
        profile.restaurant_id ||
        (metadata as any).restaurantId ||
        (metadata as any).restaurant_id;
    // Normalize: if we got an object (e.g. {id: "..."}), extract id
    if (restaurantId && typeof restaurantId === "object" && restaurantId !== null && "id" in restaurantId) {
        restaurantId = (restaurantId as { id?: string }).id;
    }
    if (restaurantId && typeof restaurantId !== "string") {
        restaurantId = String(restaurantId);
    }

    // Fallback: extract from Lua bridge uid / sessionId
    // ("…-tenant-<UUID>-user-<UUID>…" or "tenant-<UUID>-user-<UUID>")
    if (!restaurantId) {
        const sessionId =
            (metadata as any).sessionId ||
            profile.sessionId ||
            userData.sessionId ||
            (user as any)?.sessionId ||
            (user as any)?.uid;
        const fromBridge = extractRestaurantIdFromLuaBridgeId(sessionId);
        if (fromBridge) {
            restaurantId = fromBridge;
            console.log(`[agentContext] Extracted restaurantId from Lua bridge id: ${restaurantId}`);
        }
    }

    // Fallback: extract from runtimeContext string (LuaPop passes "Restaurant: Name (ID: <uuid>)")
    if (!restaurantId) {
        const rc = profile.runtimeContext || (user as any)?.runtimeContext || userData.runtimeContext;
        if (rc && typeof rc === "string") {
            const match = rc.match(/\(ID:\s*([^)]+)\)/i) || rc.match(/RestaurantID:\s*([^,\s]+)/i);
            if (match) {
                restaurantId = match[1].trim();
                console.log(`[agentContext] Extracted restaurantId from runtimeContext: ${restaurantId}`);
            }
        }
    }

    // Fallback: check uid format "whatsapp:<phone>" - extract from conversation context
    if (!restaurantId && (user as any)?.context) {
        const ctx = String((user as any).context);
        const match = ctx.match(/Restaurant ID:\s*([0-9a-f-]{36})/i) || ctx.match(/\(ID:\s*([^)]+)\)/i);
        if (match) {
            restaurantId = match[1].trim();
            console.log(`[agentContext] Extracted restaurantId from user.context: ${restaurantId}`);
        }
    }

    // Fallback: call /api/auth/agent-context/ with user token (dashboard users) to resolve restaurant
    if (!restaurantId) {
        const userToken =
            (user as any)?.token ||
            userData.token ||
            profile.token ||
            profile.accessToken ||
            (metadata as any).token ||
            (metadata as any).accessToken;
        if (userToken && typeof userToken === "string" && userToken.length > 50) {
            try {
                const baseUrl = env("API_BASE_URL") || process.env.API_BASE_URL || "http://localhost:8000";
                const res = await fetch(`${baseUrl}/api/auth/agent-context/`, {
                    headers: { Authorization: `Bearer ${userToken}` },
                });
                if (res.ok) {
                    const data = (await res.json()) as { restaurant?: { id?: string } };
                    const rid = data?.restaurant?.id;
                    if (rid && typeof rid === "string") {
                        restaurantId = rid;
                        console.log(`[agentContext] Resolved restaurantId from agent-context API: ${restaurantId}`);
                    }
                }
            } catch (e) {
                console.warn("[agentContext] agent-context fallback failed:", (e as Error)?.message);
            }
        }
    }

    // --- User ID (Mizan CustomUser UUID, not Lua's internal id) ---
    let userId = resolveMizanUserIdFromUser(user);
    if (!userId) {
        userId =
            extractMizanUserIdFromLuaBridgeId((user as any)?.uid) ||
            extractMizanUserIdFromLuaBridgeId(profile.sessionId) ||
            extractMizanUserIdFromLuaBridgeId((metadata as any).sessionId);
    }

    // --- Phone (critical for WhatsApp users) ---
    const uid = (user as any)?.uid;
    const phoneFromUid = uid && String(uid).includes(":") ? String(uid).split(":")[1] : uid;
    const phoneFromData = userData.phone ?? profile.phoneNumber ?? profile.mobileNumber ?? (metadata as any).phone;
    const rawPhone = [phoneFromData, phoneFromUid].find(
        (p: any) => p && String(p).replace(/[^0-9]/g, "").length >= 6
    );
    const phone = rawPhone ? String(rawPhone).replace(/[^0-9]/g, "") : undefined;

    const emailGuess =
        (typeof userData.email === "string" && userData.email.trim()) ||
        (typeof userData.emailAddress === "string" && userData.emailAddress.trim()) ||
        (typeof profile.email === "string" && profile.email.trim()) ||
        (typeof profile.emailAddress === "string" && profile.emailAddress.trim()) ||
        (typeof (metadata as any).email === "string" && (metadata as any).email.trim()) ||
        (typeof (metadata as any).emailAddress === "string" &&
            (metadata as any).emailAddress.trim());

    // --- Agent key (stable, never expires) ---
    const agentKey =
        env("LUA_WEBHOOK_API_KEY") || env("WEBHOOK_API_KEY") || env("MIZAN_SERVICE_TOKEN");

    // --- User token (JWT from dashboard, may be expired) ---
    const userToken =
        (user as any)?.token ||
        userData.token ||
        profile.token ||
        profile.accessToken ||
        profile.credentials?.accessToken ||
        (metadata as any).token ||
        (metadata as any).accessToken;

    // Prefer the agent key for API calls; only fall back to user JWT
    const token = agentKey || userToken || env("MIZAN_SERVICE_TOKEN");

    const sessionIdRaw =
        (metadata as any).sessionId ||
        profile.sessionId ||
        userData.sessionId ||
        (user as any)?.sessionId;
    const sessionId =
        typeof sessionIdRaw === "string" && sessionIdRaw.trim().length > 0
            ? sessionIdRaw.trim()
            : undefined;

    // Fallback: resolve tenant from Mizan user identity when Lua metadata
    // omitted restaurantId (common on WhatsApp). Same resolution rules as
    // dashboard widget agent endpoints.
    if (!restaurantId && agentKey && user) {
        const body: Record<string, string> = {};
        if (userId) body.user_id = userId;
        if (phone) body.phone = phone;
        if (emailGuess) body.email = emailGuess;
        if (Object.keys(body).length > 0) {
            try {
                const baseUrl = env("API_BASE_URL") || process.env.API_BASE_URL || "http://localhost:8000";
                const res = await fetch(`${baseUrl}/api/dashboard/agent/widgets/resolve-tenant/`, {
                    method: "POST",
                    headers: {
                        Authorization: `Bearer ${agentKey}`,
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify(body),
                });
                if (res.ok) {
                    const data = (await res.json()) as { success?: boolean; restaurant_id?: string };
                    if (data?.success && data?.restaurant_id) {
                        restaurantId = String(data.restaurant_id);
                        console.log(`[agentContext] Resolved restaurantId from resolve-tenant: ${restaurantId}`);
                    }
                }
            } catch (e) {
                console.warn("[agentContext] resolve-tenant failed:", (e as Error)?.message);
            }
        }
    }

    return { restaurantId, token, agentKey, userId, phone, email: emailGuess || undefined, userToken, sessionId };
}
