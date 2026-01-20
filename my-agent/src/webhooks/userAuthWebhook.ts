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
            userId: z.string().optional()
        }).optional()
    }).refine(data => data.emailAddress || data.mobileNumber, {
        message: "Either emailAddress or mobileNumber is required"
    }),

    execute: async ({ headers, body }) => {
        console.log(`🔐 [UserAuth] Processing auth for: ${body.emailAddress || body.mobileNumber}`);

        // Validate API key
        const expectedKey = env('WEBHOOK_API_KEY');
        if (!expectedKey) {
            throw new Error('WEBHOOK_API_KEY not configured');
        }
        if (!headers || headers['x-api-key'] !== expectedKey) {
            throw new Error('Unauthorized: Invalid API key');
        }

        // ═══════════════════════════════════════════════════════════════════════
        // IMPLEMENTATION: Requires User.create() API (NOT YET AVAILABLE)
        // ═══════════════════════════════════════════════════════════════════════
        //
        // When lua-cli adds User.create(), uncomment and use this implementation:
        //
        // Proposed User.create() API signature:
        //   User.create({
        //     emailAddress?: string,   // User's email address
        //     mobileNumber?: string,   // User's phone number (E.164 format)
        //     fullName?: string        // Optional display name
        //   }): Promise<UserDataInstance>
        //
        // The API should:
        //   - Create user if not exists (by emailAddress or mobileNumber)
        //   - Return existing user if already exists
        //   - Return the UserDataInstance for further modifications

        /*
        try {
            // Create or get existing user by emailAddress/mobileNumber
            const user = await User.create({
                emailAddress: body.emailAddress,
                mobileNumber: body.mobileNumber,
                fullName: body.fullName
            });

            // Set tenant context on the returned user instance
            user.restaurantId = body.restaurantId;
            user.restaurantName = body.restaurantName;
            user.role = body.role;
            user.permissions = body.permissions || [];
            user.lastAuthAt = new Date().toISOString();
            
            // Store any additional metadata
            if (body.metadata) {
                user.metadata = body.metadata;
            }

            // Persist changes
            await user.save();

            console.log(`✅ [UserAuth] User provisioned: ${user.id} @ ${body.restaurantName}`);

            return {
                success: true,
                userId: user.id,
                restaurantId: body.restaurantId,
                message: `User ${body.emailAddress || body.mobileNumber} linked to ${body.restaurantName}`
            };
        } catch (error) {
            console.error(`❌ [UserAuth] Failed to provision user:`, error);
            throw error;
        }
        */

        // ═══════════════════════════════════════════════════════════════════════
        // TEMPORARY: Return info about what would be created
        // ═══════════════════════════════════════════════════════════════════════
        // 
        // Until User.create() is available, this webhook logs the request and
        // returns a placeholder response. The actual user provisioning won't happen.
        //
        // WORKAROUND: Use the runtimeContext approach instead (see TenantContextPreprocessor.ts)

        console.log(`⏳ [UserAuth] User.create() not yet available - logging request only`);
        console.log(`   Would create/update user:`, {
            identifier: body.emailAddress || body.mobileNumber,
            fullName: body.fullName,
            restaurant: `${body.restaurantName} (${body.restaurantId})`,
            role: body.role
        });

        return {
            success: false,
            pending: true,
            reason: "User.create() API not yet available in lua-cli",
            workaround: "Use runtimeContext approach instead - pass context via LuaPop.init() or HTTP API",
            requestedUser: {
                emailAddress: body.emailAddress,
                mobileNumber: body.mobileNumber,
                fullName: body.fullName,
                restaurantId: body.restaurantId,
                restaurantName: body.restaurantName,
                role: body.role
            }
        };
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
