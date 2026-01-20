import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";

/**
 * Tenant Context Preprocessor
 * 
 * Validates that tenant context is available for multi-tenant operations.
 * 
 * APPROACH 1: runtimeContext (ACTIVE)
 * ------------------------------------
 * The tenant context comes from the frontend/API via runtimeContext field.
 * This is injected into the AI's prompt automatically - no preprocessing needed!
 * 
 * Frontend (LuaPop):
 *   window.LuaPop.init({
 *     agentId: "mizan-agent",
 *     sessionId: `mizan-${userId}`,
 *     runtimeContext: `Restaurant: ${restaurant.name} (ID: ${restaurant.id}), User: ${userName}, Role: ${role}`
 *   });
 * 
 * Backend (HTTP API):
 *   POST /chat/generate/mizan-agent
 *   { 
 *     messages: [...], 
 *     sessionId: "mizan-123",
 *     runtimeContext: "Restaurant: Baromètre (ID: barometre), User: Ahmed, Role: manager"
 *   }
 * 
 * The AI receives this context in its prompt and uses it when calling tools.
 * 
 * APPROACH 2: user.data (ALTERNATIVE - for persistence)
 * ------------------------------------------------------
 * If you need to persist tenant context on the user profile (e.g., set via webhook
 * when user first authenticates), the preprocessor can read from user.data.
 * See the commented-out section below.
 */
export const tenantContextPreprocessor = new PreProcessor({
    name: "tenant-context-validation",
    description: "Validates tenant context is available (context comes via runtimeContext)",
    priority: 100, // Run LATE in the pipeline to ensure our modifications stick

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        console.log("[TenantContext] Processing request", {
            uid: user.uid,
            channel,
            hasUserData: !!user.data,
            hasLuaProfile: !!(user as any)._luaProfile
        });

        // 0. Extract token from Lua profile session data (passed via LuaPop.init({ token }))
        const luaProfile = (user as any)._luaProfile || {};
        const sessionToken = luaProfile.credentials?.accessToken ||
            luaProfile.token ||
            luaProfile.sessionToken ||
            (user as any).token;

        if (sessionToken && !user.data?.token) {
            console.log("[TenantContext] 🔐 Extracted session token from profile");
            user.data = { ...user.data, token: sessionToken };
        }

        // 1. Detect context from messages (specifically the first one where runtimeContext sits)
        let detectedRestaurantId = user.data?.restaurantId;
        let detectedRestaurantName = user.data?.restaurantName;

        const firstMessage = messages[0];
        if (firstMessage && firstMessage.type === 'text') {
            const text = firstMessage.text;
            // Enhanced regex for: "Restaurant: Name (ID: id)", "User: Full Name (ID: id)", "Role: RO_LE"
            const restaurantMatch = text.match(/Restaurant:\s*([^(\n]+?)\s*\(ID:\s*([^)]+?)\)/i);
            const userMatch = text.match(/User:\s*([^(\n]+?)\s*\(ID:\s*([^)]+?)\)/i);
            const roleMatch = text.match(/Role:\s*([^,)\n]+)/i);

            if (restaurantMatch) {
                detectedRestaurantName = restaurantMatch[1].trim();
                detectedRestaurantId = restaurantMatch[2].trim();
                console.log(`[TenantContext] 🏢 Detected Restaurant: ${detectedRestaurantName} (${detectedRestaurantId})`);
                user.data = { ...user.data, restaurantId: detectedRestaurantId, restaurantName: detectedRestaurantName };
            }
            if (userMatch) {
                const userName = userMatch[1].trim();
                const userId = userMatch[2].trim();
                console.log(`[TenantContext] 👤 Detected User: ${userName} (${userId})`);
                user.data = { ...user.data, userName, userId };
            }
            if (roleMatch) {
                const role = roleMatch[1].trim();
                user.data = { ...user.data, role };
            }
        }

        // 2. If we have context, inject/update the anchoring block
        if (detectedRestaurantId) {
            console.log(`[TenantContext] ⚓ Anchoring context for ${detectedRestaurantName} (${detectedRestaurantId})`);

            // Only save if we actually updated something
            if (user.data?.restaurantId === detectedRestaurantId) {
                try {
                    await user.save();
                    console.log("[TenantContext] ✅ User data persisted to cloud.");
                } catch (e) {
                    console.error("[TenantContext] ❌ Failed to save user data:", e);
                }
            }

            const now = new Date();
            const contextBlock = [
                `[SYSTEM: PERSISTENT CONTEXT]`,
                `Restaurant: ${detectedRestaurantName}`,
                `Restaurant ID: ${detectedRestaurantId}`,
                `User: ${user.data?.userName || user._luaProfile?.fullName || "Manager"} (Role: ${user.data?.role || "Owner"})`,
                `Today is ${now.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}`,
                `Current Time: ${now.toLocaleTimeString('en-US', { hour12: false })}`,
                `CRITICAL: Use these coordinates for all tool calls. Do not ask for restaurant or date.`,
                `AGENT_IDENTITY_VERIFIED: TRUE`
            ].join('\n');

            // Inject into messages to ensure it's in the prompt
            const modifiedMessages = messages.map((msg, i) => {
                // Remove any existing blocks first
                if (msg.type === 'text') {
                    const cleanText = msg.text.replace(/\[SYSTEM: PERSISTENT CONTEXT\][\s\S]*?AGENT_IDENTITY_VERIFIED: TRUE/g, '').trim();

                    // Inject into the FIRST message (anchoring) AND the LATEST message (recency)
                    if (i === 0 || i === messages.length - 1) {
                        return { ...msg, text: `${contextBlock}\n\n${cleanText}` };
                    }
                    return { ...msg, text: cleanText };
                }
                return msg;
            });

            return { action: 'proceed' as const, modifiedMessage: modifiedMessages };
        }

        return { action: 'proceed' as const };

        return { action: 'proceed' as const };

        // ═══════════════════════════════════════════════════════════════════════
        // APPROACH 2: user.data persistence (COMMENTED OUT)
        // ═══════════════════════════════════════════════════════════════════════
        // 
        // Use this approach when:
        // - You want tenant context persisted on the user profile
        // - Context is set once via webhook (userAuthWebhook) when user logs in
        // - PreProcessor reads from user.data and injects into messages
        //
        // To enable: uncomment the code below and comment out the 'proceed' above

        /*
        const restaurantId = user.data?.restaurantId;
        const restaurantName = user.data?.restaurantName;
        const userRole = user.data?.role;

        if (restaurantId) {
            console.log(`[TenantContext] ✅ Found context: ${restaurantName} (${restaurantId})`);
            
            // Inject context into the first message for the agent
            const enrichedMessages = injectTenantContext(messages, {
                restaurantId,
                restaurantName: restaurantName || "Unknown Restaurant",
                role: userRole
            });

            return { action: 'proceed' as const, modifiedMessage: enrichedMessages };
        }

        // No tenant context - decide how to handle
        console.warn("[TenantContext] ⚠️ No tenant context on user.data");
        
        // Option A: Block unauthenticated users
        // return {
        //     action: 'block' as const,
        //     response: "Please log in through the Mizan app to access restaurant features."
        // };

        // Option B: Proceed without context (let agent handle it)
        return { action: 'proceed' as const };
        */
    }
});

/**
 * Helper: Injects tenant context into message text (for Approach 2)
 */
function injectTenantContext(
    messages: ChatMessage[],
    context: { restaurantId: string; restaurantName: string; role?: string }
): ChatMessage[] {
    const contextBlock = [
        `[TENANT CONTEXT]`,
        `Restaurant: ${context.restaurantName}`,
        `Restaurant ID: ${context.restaurantId}`,
        context.role ? `User Role: ${context.role}` : null
    ].filter(Boolean).join('\n');

    return messages.map((msg, index) => {
        // Only inject into the first text message
        if (index === 0 && msg.type === 'text') {
            return {
                ...msg,
                text: `${msg.text}\n\n${contextBlock}`
            };
        }
        return msg;
    });
}

export default tenantContextPreprocessor;
