/**
 * Deterministic safety-incident router — logs broken glass / slips / fire / injury
 * via the reporting API so the LLM cannot invent "unable to report the incident".
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import { extractLastUserText } from "../utils/extractLastUserText";
import { stripSystemContextBlocks } from "../utils/stripSystemContext";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";
import { isSafetyIncidentMessage } from "../shared/incidentIntent";

function phoneFromUser(user: UserDataInstance): string {
    const u = user as unknown as LuaUserPhoneSource & { uid?: string };
    return resolveStaffPhoneForByPhoneTools(
        {
            uid: u.uid,
            data: (u as { data?: Record<string, unknown> }).data,
            _luaProfile: (u as { _luaProfile?: Record<string, unknown> })._luaProfile,
        },
        null,
    );
}

function confirmationMessage(description: string, staffName: string): string {
    const name = staffName.trim() || "there";
    const snippet = description.replace(/\s+/g, " ").trim().slice(0, 260);
    return (
        `Hi ${name}, thank you for speaking up—we've logged your report and notified management so they can respond quickly.\n\n` +
        `You reported: "${snippet}"\n\n` +
        `We'll make sure the situation is checked and that everyone stays safe. If anything gets worse or someone is hurt, tell a manager on duty right away.`
    );
}

export const incidentCommandPreprocessor = new PreProcessor({
    name: "incident-command-router",
    description:
        "Detects safety incidents (broken glass, slips, fire, injury) and logs them immediately.",
    // Above Operations (105); below ClockIn (200) and StaffRequest (190).
    priority: 185,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText = stripSystemContextBlocks(extractLastUserText(messages));
        if (!isSafetyIncidentMessage(lastText)) {
            return { action: "proceed" as const };
        }

        const tenant = await resolveTenantForUser(user);
        const phone = phoneFromUser(user) || tenant.phone || "";

        if (!tenant.restaurantId) {
            console.warn(
                `[IncidentCommandPreprocessor] No restaurant for safety report; channel=${channel}`,
            );
            return {
                action: "block" as const,
                response:
                    "I couldn't link this report to your workspace yet. Please message from your registered staff WhatsApp number, or open Miya from the Mizan app, then try again.",
            };
        }

        console.log(
            `[IncidentCommandPreprocessor] Logging safety incident restaurant=${tenant.restaurantId} phone=${phone ? "***" + phone.slice(-4) : "-"} text=${JSON.stringify(lastText.slice(0, 80))}`,
        );

        const api = new ApiService();
        let staffName = "there";
        if (phone) {
            try {
                const lookup = await api.getStaffByPhoneForAgent(phone);
                if (lookup.success && lookup.found && lookup.staff?.first_name) {
                    staffName = lookup.staff.first_name;
                }
            } catch {
                /* best-effort */
            }
        }

        try {
            const result = await api.createIncidentReportForAgent({
                restaurant_id: tenant.restaurantId,
                title: "Safety incident",
                description: lastText.trim().slice(0, 2000),
                category: "Safety",
                priority: "HIGH",
                reporter_phone: phone || undefined,
            });

            if (result.success === false || result.error) {
                console.error(
                    "[IncidentCommandPreprocessor] createIncident failed:",
                    result.error || result,
                );
                // Fallback: staff_request so the report is never lost
                const fallback = await api.createStaffRequestForAgent({
                    restaurant_id: tenant.restaurantId,
                    subject: `Safety: ${lastText.trim().slice(0, 80)}`,
                    description: lastText.trim().slice(0, 2000),
                    category: "OTHER",
                    priority: "HIGH",
                    phone: phone || undefined,
                    follow_up_enabled: true,
                });
                if (fallback.success !== false) {
                    return {
                        action: "block" as const,
                        response: confirmationMessage(lastText, staffName),
                        metadata: { incident_fallback: "staff_request", record_id: fallback.id },
                    };
                }
                return {
                    action: "block" as const,
                    response:
                        "I couldn't save that report just now. Please tell a manager on duty right away, and try messaging me again in a moment.",
                };
            }

            return {
                action: "block" as const,
                response: confirmationMessage(lastText, staffName),
                metadata: {
                    incident_status: "success",
                    incident_id: (result as { id?: string }).id,
                },
            };
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[IncidentCommandPreprocessor] threw:", em);
            return {
                action: "block" as const,
                response:
                    "I couldn't save that report just now. Please tell a manager on duty right away, and try messaging me again in a moment.",
            };
        }
    },
});

export default incidentCommandPreprocessor;
