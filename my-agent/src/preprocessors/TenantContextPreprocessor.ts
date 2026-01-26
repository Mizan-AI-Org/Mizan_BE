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
        console.log("[TenantContext] V8 Processing request", {
            uid: user.uid,
            channel,
            hasUserData: !!user.data,
            dataKeys: user.data ? Object.keys(user.data).join(',') : 'none',
            hasLuaProfile: !!(user as any)._luaProfile
        });

        // 0. Extract token from Lua profile session data (passed via LuaPop.init({ token }))
        const luaProfile = (user as any)._luaProfile || {};
        console.log(`[TenantContext] Profile debug keys: ${Object.keys(luaProfile).join(', ')}`);

        const sessionToken = (user as any).token ||
            luaProfile.accessToken ||
            luaProfile.credentials?.accessToken ||
            luaProfile.token ||
            luaProfile.sessionToken;

        if (sessionToken && user.data?.token !== sessionToken) {
            console.log(`[TenantContext] 🔐 Syncing token to user.data and instance (length: ${sessionToken.length})`);
            user.data = { ...user.data, token: sessionToken };
            // Also sync directly to the user instance for maximum tool compatibility
            (user as any).token = sessionToken;
        } else if (!sessionToken) {
            console.warn(`[TenantContext] ⚠️ No sessionToken (accessToken/token/sessionToken) found in profile.`);
        }

        // 1. Detect context from ALL messages (in case it's not the first one)
        let detectedRestaurantId = user.data?.restaurantId;
        let detectedRestaurantName = user.data?.restaurantName;
        let detectedToken = user.data?.token;

        for (const msg of messages) {
            if (msg.type === 'text') {
                const text = msg.text;
                // Flexible regex for different formats
                const restaurantMatch = text.match(/Restaurant:\s*([^(\n]+?)\s*\(ID:\s*([^)]+?)\)/i) || text.match(/RestaurantID:\s*([^,\n]+)/i);
                const userMatch = text.match(/User:\s*([^(\n]+?)\s*\(ID:\s*([^)]+?)\)/i);
                // JWT tokens can be very long and may be followed by commas in runtimeContext
                // Match "Token: <jwt>" where jwt is base64url encoded (A-Za-z0-9-_ and .)
                // Capture until we hit a comma, closing paren, newline, or end of line
                const tokenMatch = text.match(/Token:\s*([A-Za-z0-9\-_\.]+(?:\.[A-Za-z0-9\-_\.]+)*)/i) || 
                                   text.match(/accessToken:\s*([A-Za-z0-9\-_\.]+(?:\.[A-Za-z0-9\-_\.]+)*)/i);

                if (restaurantMatch) {
                    detectedRestaurantName = restaurantMatch[1].trim();
                    detectedRestaurantId = restaurantMatch[2] ? restaurantMatch[2].trim() : detectedRestaurantName;
                    console.log(`[TenantContext] 🏢 Detected Restaurant: ${detectedRestaurantName} (${detectedRestaurantId})`);
                }
                if (userMatch) {
                    const userName = userMatch[1].trim();
                    const userId = userMatch[2].trim();
                    console.log(`[TenantContext] 👤 Detected User: ${userName} (${userId})`);
                    user.data = { ...user.data, userName, userId };
                }
                if (tokenMatch && tokenMatch[1]) {
                    const t = tokenMatch[1].trim();
                    if (t && t !== "undefined" && t !== "null" && t.length > 50) {
                        // JWT tokens are typically 100+ characters, so this filters out false matches
                        detectedToken = t;
                        console.log(`[TenantContext] 🔑 Detected JWT Token from message (length: ${t.length})`);
                    }
                }
            }
        }

        if (detectedRestaurantId) user.data = { ...user.data, restaurantId: detectedRestaurantId, restaurantName: detectedRestaurantName };
        if (detectedToken) {
            user.data = { ...user.data, token: detectedToken };
            (user as any).token = detectedToken;
            console.log(`[TenantContext] ✅ Token saved to user.data.token (length: ${detectedToken.length})`);
        }

        // Always save token if we have it, even if restaurant isn't detected yet
        if (detectedToken && (!user.data?.token || user.data.token !== detectedToken)) {
            user.data = { ...user.data, token: detectedToken };
            (user as any).token = detectedToken;
            try {
                await user.save();
                console.log("[TenantContext] ✅ Token persisted immediately");
            } catch (e) {
                console.error("[TenantContext] ❌ Failed to persist token:", e);
            }
        }

        // 2. If we have context, inject/update the anchoring block
        if (detectedRestaurantId) {
            console.log(`[TenantContext] ⚓ Anchoring context for ${detectedRestaurantName} (${detectedRestaurantId})`);

            // Always attempt to save if user.data exists and was likely modified
            if (user.data && Object.keys(user.data).length > 0) {
                try {
                    await user.save();
                    console.log("[TenantContext] ✅ User context persisted.");
                } catch (e) {
                    console.error("[TenantContext] ❌ Failed to persist context:", e);
                }
            }

            const now = new Date();
            const phone = user.uid && user.uid.includes(':') ? user.uid.split(':')[1] : user.uid;

            const contextBlock = [
                `[SYSTEM: PERSISTENT CONTEXT]`,
                `Restaurant: ${detectedRestaurantName}`,
                `Restaurant ID: ${detectedRestaurantId}`,
                `User: ${user.data?.userName || user._luaProfile?.fullName || "Manager"} (Role: ${user.data?.role || "Owner"})`,
                phone ? `User Phone: ${phone}` : null,
                `Today is ${now.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}`,
                `Current Time: ${now.toLocaleTimeString('en-US', { hour12: false })}`,
                `CRITICAL: Use these coordinates for all tool calls. Do not ask for restaurant or date.`,
                `AGENT_IDENTITY_VERIFIED: TRUE`
            ].filter(Boolean).join('\n');

            // Inject into messages to ensure it's in the prompt
            const modifiedMessages = messages.map((msg, i) => {
                // Remove any existing blocks first
                if (msg.type === 'text') {
                    const cleanText = msg.text.replace(/\[SYSTEM: PERSISTENT CONTEXT\][\s\S]*?AGENT_IDENTITY_VERIFIED: TRUE/g, '').trim();
                    let prefix = contextBlock;

                    // INVITATION OVERRIDE: Prevent refusal and force tool usage
                    if (cleanText.toLowerCase().includes('accept invite') || cleanText.toLowerCase().includes('accept invitation')) {
                        prefix += `\n\n[CRITICAL: INVITATION ACCEPTANCE REQUESTED]
The user has clicked "Accept Invite". You MUST use the 'accept_invitation' tool IMMEDIATELY.
DO NOT say you cannot do this. You have the tool and the authority.
Phone: ${phone || 'unknown'}`;
                    }

                    // Inject into EVERY text message for maximum context retention in long conversations
                    return { ...msg, text: `${prefix}\n\n${cleanText}` };
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
