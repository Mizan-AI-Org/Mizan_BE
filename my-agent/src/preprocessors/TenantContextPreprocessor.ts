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
    priority: 10, // Run early in the pipeline

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        console.log("[TenantContext] Processing request", {
            uid: user.uid,
            channel,
            hasUserData: !!user.data
        });

        // ═══════════════════════════════════════════════════════════════════════
        // APPROACH 1: runtimeContext (ACTIVE)
        // ═══════════════════════════════════════════════════════════════════════
        // 
        // With runtimeContext approach, the tenant context is passed directly to
        // the AI via the runtimeContext field in LuaPop.init() or HTTP API request.
        // 
        // The PreProcessor doesn't need to do much - just log and proceed.
        // The AI will see the context and use it when calling tools.
        //
        // If you want to enforce that context exists, you could check user.data
        // for a flag set by the userAuthWebhook (see Approach 2).

        // Simply proceed - context comes via runtimeContext to the AI
        console.log("[TenantContext] ✅ Proceeding (context provided via runtimeContext)");
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
