import { PreProcessor, Message } from "lua-cli";

export const tenantContextPreprocessor: PreProcessor = {
    name: "tenant-context-enrichment",
    description: "Extracts restaurant context from user traits and enriches the conversation session",

    execute: async (message: any, context: any) => {
        // Extract traits passed from the frontend widget via sessionId workaround
        // Format: tenant-<id>-name-<base64Name>-user-<uid>
        const sessionId = context.sessionId || "";
        let restaurantId = context.user?.traits?.restaurant_id;
        let restaurantName = context.user?.traits?.restaurant_name;

        // If not in traits, try to parse from sessionId
        if (!restaurantId && sessionId.startsWith("tenant-")) {
            try {
                const parts = sessionId.split("-");
                // tenant-<id> is parts[1]
                // name-<base64> is parts[3]
                // user-<uid> is parts[5]

                // Find indices dynamically to be safe
                const tenantIndex = parts.indexOf("tenant");
                const nameIndex = parts.indexOf("name");

                if (tenantIndex !== -1 && parts[tenantIndex + 1]) {
                    restaurantId = parts[tenantIndex + 1];
                }

                if (nameIndex !== -1 && parts[nameIndex + 1]) {
                    const encodedName = parts[nameIndex + 1];
                    try {
                        restaurantName = decodeURIComponent(atob(encodedName));
                    } catch (e) {
                        console.warn("[TenantContext] Failed to decode restaurant name:", e);
                        restaurantName = "Unknown Restaurant";
                    }
                }
            } catch (e) {
                console.error("[TenantContext] Failed to parse sessionId:", e);
            }
        }

        if (restaurantId) {
            // Store in the conversation context for tools to access
            context.set("restaurantId", restaurantId);
            context.set("restaurantName", restaurantName || "Unknown Restaurant");

            console.log(`[TenantContext] Enriched context for Restaurant: ${restaurantName} (${restaurantId})`);
        } else {
            console.warn("[TenantContext] No restaurant_id found in traits or sessionId");
        }

        return message;
    }
};
