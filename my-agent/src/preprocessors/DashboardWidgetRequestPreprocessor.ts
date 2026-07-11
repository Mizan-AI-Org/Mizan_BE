/**
 * Runs dashboard_widgets when a manager asks to create/add a dashboard widget.
 * Blocks with the backend message so the LLM cannot invent "temporary technical issue" apologies.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import DashboardWidgetsTool from "../skills/tools/DashboardWidgetsTool";
import {
    resolveDashboardWidgetIntent,
    sanitizeWidgetUserText,
} from "../skills/tools/dashboardWidgetIntent";
import { extractLastUserText } from "../utils/extractLastUserText";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";

const dashboardWidgetsTool = new DashboardWidgetsTool();

export const dashboardWidgetRequestPreprocessor = new PreProcessor({
    name: "dashboard-widget-router",
    description:
        "Detects manager requests to create/add dashboard widgets and executes dashboard_widgets immediately.",
    // Run AFTER tenant-context-validation (priority 100) so restaurantId is available on WhatsApp.
    priority: 110,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        // LanguageMirror (priority 2) prefixes [REPLY LANGUAGE] onto the last user
        // message — never use that raw string as a widget title.
        const lastText = sanitizeWidgetUserText(extractLastUserText(messages));
        const intent = resolveDashboardWidgetIntent(lastText);
        if (!intent) {
            return { action: "proceed" as const };
        }

        const tenant = await resolveTenantForUser(user);
        const restaurantId = tenant.restaurantId;
        console.log(
            `[DashboardWidgetPreprocessor] Widget request detected action=${intent.action} channel=${channel} restaurant=${restaurantId || "?"} user=${tenant.userId || "?"} email=${tenant.email || "?"}`,
        );

        let toolResult: Record<string, unknown> = {};
        try {
            if (intent.action === "add") {
                toolResult = (await dashboardWidgetsTool.execute({
                    action: "add",
                    widgets: intent.widgets,
                    restaurantId,
                    user_id: tenant.userId,
                    email: tenant.email,
                    phone: tenant.phone,
                })) as Record<string, unknown>;
            } else {
                toolResult = (await dashboardWidgetsTool.execute({
                    action: "create_custom",
                    title: intent.title,
                    source_text: intent.sourceText,
                    restaurantId,
                    add_to_dashboard: true,
                    user_id: tenant.userId,
                    email: tenant.email,
                    phone: tenant.phone,
                })) as Record<string, unknown>;
            }
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[DashboardWidgetPreprocessor] dashboard_widgets threw:", em);
            toolResult = {
                status: "error",
                message:
                    "I couldn't update your dashboard layout right now. Please refresh Mizan and try again.",
            };
        }

        const message = String(toolResult.message || "").trim();
        if (message) {
            return {
                action: "block" as const,
                response: message,
                metadata: {
                    widget_action: intent.action,
                    widget_status: toolResult.status,
                    resolved_from_alias: toolResult.resolved_from_alias,
                },
            };
        }

        return { action: "proceed" as const };
    },
});

export default dashboardWidgetRequestPreprocessor;
