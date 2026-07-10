/**
 * Deterministic router for WhatsApp operational commands (procurement, maintenance,
 * finance invoices, HR reminders, record lookup). Runs after tenant context
 * so tools always have restaurant_id — prevents "problème technique" LLM failures.
 */
import ApiService from "../services/ApiService";
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import { resolveOperationsCommandIntent } from "./operationIntent";
import { executeOperationsIntent, toolMessage } from "./executeOperationsIntent";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";

export const operationsCommandPreprocessor = new PreProcessor({
    name: "operations-command-router",
    description:
        "Detects procurement, maintenance, invoice, HR/ops reminders, and lookup intents; executes immediately.",
    priority: 105,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const intent = resolveOperationsCommandIntent(messages);
        if (!intent) {
            return { action: "proceed" as const };
        }

        const tenant = await resolveTenantForUser(user);
        if (!tenant.restaurantId) {
            return { action: "proceed" as const };
        }

        console.log(
            `[OperationsCommandPreprocessor] intent=${intent.kind} channel=${channel} restaurant=${tenant.restaurantId}`,
        );

        let toolResult: Record<string, unknown> = {};
        try {
            toolResult = (await executeOperationsIntent(intent, new ApiService(), {
                restaurantId: tenant.restaurantId,
                userId: tenant.userId,
                email: tenant.email,
                phone: tenant.phone,
            })) as Record<string, unknown>;
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[OperationsCommandPreprocessor] execute threw:", em);
            toolResult = {
                status: "error",
                message: "I couldn't complete that action right now. Please try again in a moment.",
            };
        }

        const message = toolMessage(toolResult);
        if (message) {
            return {
                action: "block" as const,
                response: message,
                metadata: {
                    operations_intent: intent.kind,
                    operations_status: toolResult.status,
                    task_ref: toolResult.task_ref,
                    record_id: toolResult.record_id || toolResult.recordId,
                },
            };
        }

        return { action: "proceed" as const };
    },
});

export default operationsCommandPreprocessor;
