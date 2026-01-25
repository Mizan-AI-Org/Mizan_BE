/**
 * Identity Resolution Tool
 * 
 * This tool allows the agent to capture and persist identity information
 * from the runtime context into the user's data store.
 * 
 * The LLM receives runtimeContext in its prompt, but tools and preprocessors
 * don't have direct access. This tool bridges that gap by having the agent
 * explicitly capture the identity when needed.
 */

import { LuaTool, User } from "lua-cli";
import { z } from "zod";

export default class IdentityResolutionTool implements LuaTool {
    name = "resolve_identity";
    description = `Use this tool to capture and store the user's identity information from the conversation context. 
Call this when:
1. The user asks "Who am I?" and you have their name from the [SYSTEM: PERSISTENT CONTEXT] block
2. You need to confirm the user's restaurant membership
3. The user's identity needs to be persisted for future conversations

Extract the user's full name, restaurant name, and role from the context you have access to.`;

    inputSchema = z.object({
        fullName: z.string().describe("The user's full name extracted from context (e.g., 'Adam Jaidi')"),
        restaurantId: z.string().describe("The restaurant ID extracted from context"),
        restaurantName: z.string().describe("The restaurant name extracted from context"),
        role: z.string().optional().describe("The user's role (e.g., 'OWNER', 'MANAGER')"),
    });

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        console.log("[IdentityResolution] Capturing identity:", input);

        try {
            // Get the current user from context if available
            const user = context?.user;

            if (user) {
                // Persist the identity to user.data
                user.fullName = input.fullName;
                user.restaurantId = input.restaurantId;
                user.restaurantName = input.restaurantName;
                user.role = input.role || 'OWNER';
                user.identityVerifiedAt = new Date().toISOString();

                if (user.save) {
                    await user.save();
                    console.log(`[IdentityResolution] ✅ Identity persisted for ${input.fullName} @ ${input.restaurantName}`);
                }
            }

            return {
                status: "success",
                message: `Identity confirmed. You are ${input.fullName}, ${input.role || 'Owner'} at ${input.restaurantName}.`,
                identity: {
                    fullName: input.fullName,
                    restaurantId: input.restaurantId,
                    restaurantName: input.restaurantName,
                    role: input.role
                }
            };
        } catch (error) {
            console.error("[IdentityResolution] ❌ Failed to persist identity:", error);
            return {
                status: "error",
                message: `Failed to save identity: ${(error as Error).message}`
            };
        }
    }
}
