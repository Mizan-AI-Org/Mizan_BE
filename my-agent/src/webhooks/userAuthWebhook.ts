/**
 * User Authentication Webhook
 * 
 * Called by the Mizan backend when a user logs in to provision their
 * Lua user profile with tenant context.
 * 
 * WEBHOOK APPROACH FOR TENANT CONTEXT
 * ====================================
 * 
 * Flow:
 * 1. User logs into Mizan app (Django backend)
 * 2. Django calls this webhook with user + restaurant info
 * 3. Webhook creates/updates Lua user with tenant context
 * 4. User opens chat - their profile already has restaurantId
 * 5. PreProcessor reads user.data.restaurantId
 * 
 * Webhook URL (after deploy):
 *   https://webhook.heylua.ai/{agentId}/user-authenticated
 * 
 * Django integration example:
 * ```python
 * # In Django login view or signal
 * import requests
 * 
 * def on_user_login(user):
 *     requests.post(
 *         f'https://webhook.heylua.ai/{AGENT_ID}/user-authenticated',
 *         headers={'X-API-Key': WEBHOOK_API_KEY},
 *         json={
 *             'emailAddress': user.email,
 *             'mobileNumber': user.phone,
 *             'fullName': user.get_full_name(),
 *             'restaurantId': user.restaurant.id,
 *             'restaurantName': user.restaurant.name,
 *             'role': user.role,
 *             'permissions': list(user.get_all_permissions())
 *         }
 *     )
 * ```
 * 
 * CURRENT STATUS: Requires User.create() API (not yet available in lua-cli)
 * When available, uncomment the implementation below.
 */

import { LuaWebhook, User, env } from "lua-cli";
import { z } from "zod";

const userAuthWebhook = new LuaWebhook({
    name: "user-authenticated",
    description: "Provisions Lua user with tenant context when user logs into Mizan",

    headerSchema: z.object({
        'x-api-key': z.string(),
        'content-type': z.string().optional()
    }),

    bodySchema: z.object({
        // User identification (at least one required)
        emailAddress: z.string().email().optional(),
        mobileNumber: z.string().optional().nullable(),

        // User info
        fullName: z.string().optional(),

        // Tenant context (required)
        restaurantId: z.string(),
        restaurantName: z.string(),

        // Permissions
        role: z.string().optional(), // Accept any role string (will be normalized)
        permissions: z.array(z.string()).optional(),

        // Metadata including token
        metadata: z.object({
            token: z.string().optional(),
            userId: z.string().optional(),
            sessionId: z.string().optional(),
        }).optional()
    }).refine(data => data.emailAddress || data.mobileNumber, {
        message: "Either emailAddress or mobileNumber is required"
    }),

    execute: async ({ headers, body }) => {
        console.log(`🔐 [UserAuth] Processing auth for: ${body.emailAddress || body.mobileNumber}`);

        // Validate API key (backend default matches `mizan-backend/accounts/services.py`)
        const expectedKey =
            env('WEBHOOK_API_KEY') ||
            env('LUA_WEBHOOK_API_KEY') ||
            'mizan-agent-webhook-secret-2026';
        if (!headers || headers['x-api-key'] !== expectedKey) {
            throw new Error('Unauthorized: Invalid API key');
        }

        // We can't create Lua users yet (no User.create()), but we *can* update an existing one.
        // The frontend provides a deterministic `sessionId`; backend passes it in `metadata.sessionId`.
        const tokenFromBackend = body.metadata?.token;
        const sessionId = body.metadata?.sessionId;

        const normalizedMobile = (body.mobileNumber || '').replace(/[^\d+]/g, '');

        const candidates = [
            sessionId,
            body.emailAddress ? `email:${body.emailAddress}` : undefined,
            body.emailAddress,
            normalizedMobile ? `phone:${normalizedMobile}` : undefined,
            normalizedMobile ? `whatsapp:${normalizedMobile}` : undefined,
        ].filter(Boolean) as string[];

        console.log(`[UserAuth] Attempting to locate Lua user. Candidates: ${candidates.join(' | ')}`);

        let foundUid: string | null = null;
        let luaUser: any = null;

        for (const uid of candidates) {
            try {
                const maybe = await User.get(uid);
                if (maybe) {
                    luaUser = maybe;
                    foundUid = uid;
                    break;
                }
            } catch {
                // Try next candidate
            }
        }

        if (!luaUser) {
            console.warn(`[UserAuth] No Lua user found to update (user likely hasn't opened chat yet).`);
            return { success: false, pending: true, reason: "Lua user not found", candidates };
        }

        const nextData = {
            ...(luaUser.data || {}),
            restaurantId: body.restaurantId,
            restaurantName: body.restaurantName,
            role: body.role,
            permissions: body.permissions || [],
            userId: body.metadata?.userId,
            token: tokenFromBackend || (luaUser.data || {}).token,
            lastAuthAt: new Date().toISOString(),
        };

        luaUser.data = nextData;
        luaUser.restaurantId = body.restaurantId;
        luaUser.restaurantName = body.restaurantName;
        luaUser.role = body.role;
        if (tokenFromBackend) luaUser.token = tokenFromBackend;

        await luaUser.save();

        console.log(`✅ [UserAuth] Updated Lua user (${foundUid}) with tenant context + token=${!!tokenFromBackend}`);

        return { success: true, updatedUid: foundUid, restaurantId: body.restaurantId, hasToken: !!tokenFromBackend };
    }
});

export default userAuthWebhook;

// ═══════════════════════════════════════════════════════════════════════════════
// ALTERNATIVE IMPLEMENTATION: Using existing User.get() with known userId
// ═══════════════════════════════════════════════════════════════════════════════
//
// If you already have a mapping of Django userId -> Lua userId, you can use
// the existing User.get(userId) API:
//
// const updateExistingUserWebhook = new LuaWebhook({
//     name: "update-user-context",
//     execute: async ({ body }) => {
//         const { luaUserId, restaurantId, restaurantName, role } = body;
//         
//         // Get existing user by their Lua ID
//         const user = await User.get(luaUserId);
//         
//         // Update tenant context
//         user.restaurantId = restaurantId;
//         user.restaurantName = restaurantName;
//         user.role = role;
//         await user.save();
//         
//         return { success: true };
//     }
// });
//
// This requires storing the luaUserId in your Django database when the user
// first interacts with the chat (e.g., via a tool that captures it).
