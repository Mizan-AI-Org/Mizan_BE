/**
 * Resolve Mizan workspace (restaurant_id) and manager identity for a Lua conversation user.
 */
import { env } from "lua-cli";
import type { UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import {
    extractMizanUserIdFromLuaBridgeId,
    extractRestaurantIdFromLuaBridgeId,
} from "./extractLuaBridgeContext";
import { resolveStaffPhoneForByPhoneTools } from "./resolveStaffPhoneFromLuaUser";

export type TenantResolution = {
    restaurantId?: string;
    restaurantName?: string;
    userId?: string;
    email?: string;
    phone?: string;
};

function asStr(v: unknown): string | undefined {
    return typeof v === "string" && v.trim().length > 0 ? v.trim() : undefined;
}

function asEmail(v: unknown): string | undefined {
    if (typeof v !== "string") return undefined;
    const t = v.trim();
    return t.includes("@") ? t : undefined;
}

function collectBridgeFromValue(
    value: unknown,
    acc: { restaurantId?: string; userId?: string },
): void {
    if (typeof value !== "string" || !value.trim()) return;
    if (!acc.restaurantId) {
        acc.restaurantId = extractRestaurantIdFromLuaBridgeId(value);
    }
    if (!acc.userId) {
        acc.userId = extractMizanUserIdFromLuaBridgeId(value);
    }
}

function deepScanBridgeContext(root: unknown, depth = 0): { restaurantId?: string; userId?: string } {
    const acc: { restaurantId?: string; userId?: string } = {};
    if (depth > 4 || root == null) return acc;

    if (typeof root === "string") {
        collectBridgeFromValue(root, acc);
        return acc;
    }

    if (Array.isArray(root)) {
        for (const item of root) {
            const nested = deepScanBridgeContext(item, depth + 1);
            acc.restaurantId ||= nested.restaurantId;
            acc.userId ||= nested.userId;
            if (acc.restaurantId && acc.userId) break;
        }
        return acc;
    }

    if (typeof root === "object") {
        for (const value of Object.values(root as Record<string, unknown>)) {
            const nested = deepScanBridgeContext(value, depth + 1);
            acc.restaurantId ||= nested.restaurantId;
            acc.userId ||= nested.userId;
            if (acc.restaurantId && acc.userId) break;
        }
    }
    return acc;
}

/** Mizan CustomUser UUID — never Lua's internal profile.userId. */
export function resolveMizanUserIdFromLuaUser(user: UserDataInstance): string | undefined {
    const profile = (user as { _luaProfile?: Record<string, unknown> })._luaProfile || {};
    const metadata =
        profile.metadata && typeof profile.metadata === "object"
            ? (profile.metadata as Record<string, unknown>)
            : {};
    const data = (user.data || {}) as Record<string, unknown>;
    const luaPlatformId = asStr(profile.userId);

    const bridge = deepScanBridgeContext({
        uid: user.uid,
        sessionId: (user as { sessionId?: unknown }).sessionId,
        conversationId: (user as { conversationId?: unknown }).conversationId,
        profile,
        metadata,
        data,
    });

    const candidates = [
        asStr(data.mizanUserId),
        asStr(data.backendUserId),
        asStr(metadata.userId),
        asStr(metadata.mizanUserId),
        asStr(metadata.backendUserId),
        bridge.userId,
        extractMizanUserIdFromLuaBridgeId(metadata.sessionId),
        extractMizanUserIdFromLuaBridgeId(profile.sessionId),
        asStr(data.userId),
        asStr(data.user_id),
    ];

    for (const id of candidates) {
        if (!id) continue;
        // Skip ids that are just Lua's platform user record, not Mizan CustomUser.
        if (luaPlatformId && id === luaPlatformId) continue;
        return id;
    }
    return undefined;
}

function resolveEmailFromLuaUser(user: UserDataInstance): string | undefined {
    const profile = (user as { _luaProfile?: Record<string, unknown> })._luaProfile || {};
    const metadata =
        profile.metadata && typeof profile.metadata === "object"
            ? (profile.metadata as Record<string, unknown>)
            : {};
    const data = (user.data || {}) as Record<string, unknown>;

    return (
        asEmail(profile.emailAddress) ||
        asEmail(profile.email) ||
        asEmail(data.email) ||
        asEmail(data.emailAddress) ||
        asEmail(metadata.email) ||
        asEmail(metadata.emailAddress)
    );
}

function resolveJwtFromLuaUser(user: UserDataInstance): string | undefined {
    const profile = (user as { _luaProfile?: Record<string, unknown> })._luaProfile || {};
    const metadata =
        profile.metadata && typeof profile.metadata === "object"
            ? (profile.metadata as Record<string, unknown>)
            : {};
    const data = (user.data || {}) as Record<string, unknown>;

    const token =
        asStr((user as { token?: unknown }).token) ||
        asStr(data.token) ||
        asStr(metadata.token) ||
        asStr(metadata.accessToken) ||
        asStr(profile.accessToken) ||
        asStr(profile.token);

    return token && token.length > 50 ? token : undefined;
}

async function resolveFromAgentContextJwt(
    token: string,
): Promise<{ restaurantId?: string; restaurantName?: string; userId?: string; email?: string }> {
    const baseUrl = env("API_BASE_URL") || process.env.API_BASE_URL || "https://api.heymizan.ai";
    const res = await fetch(`${baseUrl}/api/auth/agent-context/`, {
        headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return {};
    const data = (await res.json()) as {
        user?: { id?: string; email?: string };
        restaurant?: { id?: string; name?: string };
    };
    return {
        restaurantId: data.restaurant?.id,
        restaurantName: data.restaurant?.name,
        userId: data.user?.id,
        email: data.user?.email,
    };
}

async function resolveFromTenantApi(body: Record<string, string>): Promise<{
    restaurantId?: string;
    userId?: string;
    email?: string;
}> {
    const agentKey =
        env("LUA_WEBHOOK_API_KEY") || env("WEBHOOK_API_KEY") || env("MIZAN_SERVICE_TOKEN");
    if (!agentKey || Object.keys(body).length === 0) return {};

    const baseUrl = env("API_BASE_URL") || process.env.API_BASE_URL || "https://api.heymizan.ai";
    const res = await fetch(`${baseUrl}/api/dashboard/agent/widgets/resolve-tenant/`, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${agentKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
    });
    const payload = (await res.json()) as {
        success?: boolean;
        restaurant_id?: string;
        user_id?: string;
        email?: string;
    };
    if (!payload?.success || !payload.restaurant_id) {
        if (!res.ok) {
            console.warn(
                `[resolveTenantForUser] resolve-tenant HTTP ${res.status}: ${JSON.stringify(payload).slice(0, 200)}`,
            );
        }
        return {};
    }
    return {
        restaurantId: String(payload.restaurant_id),
        userId: payload.user_id,
        email: payload.email,
    };
}

export async function resolveTenantForUser(
    user: UserDataInstance,
): Promise<TenantResolution> {
    const profile = (user as { _luaProfile?: Record<string, unknown> })._luaProfile || {};
    const metadata =
        profile.metadata && typeof profile.metadata === "object"
            ? (profile.metadata as Record<string, unknown>)
            : {};
    const data = (user.data || {}) as Record<string, unknown>;

    const bridge = deepScanBridgeContext({
        uid: user.uid,
        sessionId: (user as { sessionId?: unknown }).sessionId,
        conversationId: (user as { conversationId?: unknown }).conversationId,
        profile,
        metadata,
        data,
    });

    let restaurantId =
        asStr(data.restaurantId) ||
        asStr(metadata.restaurantId) ||
        asStr(metadata.restaurant_id) ||
        asStr(profile.restaurantId) ||
        asStr(profile.restaurant_id) ||
        bridge.restaurantId;

    let restaurantName =
        asStr(data.restaurantName) ||
        asStr(metadata.restaurantName) ||
        asStr(profile.restaurantName);

    let userId = resolveMizanUserIdFromLuaUser(user);
    let email = resolveEmailFromLuaUser(user);
    const phone = resolveStaffPhoneForByPhoneTools(
        { uid: user.uid, data, _luaProfile: profile },
        null,
    );

    const jwt = resolveJwtFromLuaUser(user);
    if (jwt) {
        try {
            const fromJwt = await resolveFromAgentContextJwt(jwt);
            restaurantId ||= fromJwt.restaurantId;
            restaurantName ||= fromJwt.restaurantName;
            userId ||= fromJwt.userId;
            email ||= fromJwt.email ? asEmail(fromJwt.email) : undefined;
            if (fromJwt.restaurantId) {
                console.log(
                    `[resolveTenantForUser] Resolved from JWT agent-context: restaurant=${fromJwt.restaurantId} user=${fromJwt.userId || "?"}`,
                );
            }
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.warn(`[resolveTenantForUser] agent-context JWT failed: ${em}`);
        }
    }

    if (!restaurantId && phone) {
        try {
            const lookup = await new ApiService().getStaffByPhoneForAgent(phone);
            if (lookup.success && lookup.found && lookup.staff?.restaurant_id) {
                restaurantId = lookup.staff.restaurant_id;
                restaurantName = lookup.staff.restaurant_name || restaurantName;
            }
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.warn(`[resolveTenantForUser] phone lookup failed: ${em}`);
        }
    }

    if (!restaurantId || !userId) {
        const body: Record<string, string> = {};
        if (userId) body.user_id = userId;
        if (email) body.email = email;
        if (phone) body.phone = phone;
        try {
            const fromApi = await resolveFromTenantApi(body);
            restaurantId ||= fromApi.restaurantId;
            userId ||= fromApi.userId;
            email ||= fromApi.email ? asEmail(fromApi.email) : undefined;
            if (fromApi.restaurantId) {
                console.log(
                    `[resolveTenantForUser] Resolved via resolve-tenant: restaurant=${fromApi.restaurantId} user=${fromApi.userId || "?"}`,
                );
            }
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.warn(`[resolveTenantForUser] resolve-tenant failed: ${em}`);
        }
    }

    if (restaurantId || userId || email || phone) {
        user.data = {
            ...data,
            ...(restaurantId ? { restaurantId, restaurantName } : {}),
            ...(userId ? { userId, mizanUserId: userId } : {}),
            ...(email ? { email } : {}),
            ...(phone ? { phone } : {}),
            ...(jwt ? { token: jwt } : {}),
        };
    }

    return { restaurantId, restaurantName, userId, email, phone };
}
