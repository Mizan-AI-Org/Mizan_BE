import ApiService from "../services/ApiService";
export const tenantContextPreprocessor = {
    name: "tenant-context-enrichment",
    description: "Extracts restaurant context from user traits and enriches the conversation session",
    execute: async (message, context) => {
        const apiService = new ApiService();
        // 1. Extract token from metadata (passed from frontend)
        const token = message.metadata?.token || context.metadata?.token;
        console.log("[TenantContext] Incoming Metadata:", {
            messageMetadata: message.metadata,
            contextMetadata: context.metadata
        });
        if (token) {
            console.log("[TenantContext] Validating user token...");
            const validationResult = await apiService.validateUser(token);
            if (validationResult.isValid) {
                // 2. Bind Context
                const { user, restaurant } = validationResult;
                context.set("user", user);
                context.set("restaurantId", restaurant.id);
                context.set("restaurantName", restaurant.name);
                context.set("restaurantData", restaurant); // Store full data for skills
                console.log(`[TenantContext] ✅ Authenticated: ${user.email} @ ${restaurant.name} (${restaurant.id})`);
                return message;
            }
            else {
                console.warn(`[TenantContext] ❌ Token validation failed: ${validationResult.error}`);
                // We could throw an error here to block the message, or let it proceed as anonymous if allowed.
                // For strict multi-tenancy, we should probably block or flag it.
                context.set("isAuthenticated", false);
                context.set("authError", validationResult.error);
            }
        }
        // Fallback to legacy sessionId parsing (TEMPORARY - for backward compatibility if needed)
        // ... (keeping existing logic as fallback or removing it if we want strict enforcement)
        // For now, let's keep the legacy logic as a fallback but log a warning
        const sessionId = context.sessionId || "";
        if (!context.get("restaurantId") && sessionId.startsWith("tenant-")) {
            console.warn("[TenantContext] ⚠️ Using insecure sessionId parsing fallback");
            // ... existing logic ...
            try {
                const parts = sessionId.split("-");
                const tenantIndex = parts.indexOf("tenant");
                const nameIndex = parts.indexOf("name");
                if (tenantIndex !== -1 && parts[tenantIndex + 1]) {
                    context.set("restaurantId", parts[tenantIndex + 1]);
                }
                if (nameIndex !== -1 && parts[nameIndex + 1]) {
                    try {
                        context.set("restaurantName", decodeURIComponent(atob(parts[nameIndex + 1])));
                    }
                    catch (e) {
                        context.set("restaurantName", "Unknown Restaurant");
                    }
                }
            }
            catch (e) {
                console.error("[TenantContext] Failed to parse sessionId:", e);
            }
        }
        if (!context.get("restaurantId")) {
            console.error("[TenantContext] ⛔ No tenant context resolved!");
        }
        return message;
    }
};
