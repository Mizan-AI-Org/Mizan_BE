/**
 * Routes staff "tell my manager / unpaid wages / payslip" through staff_request
 * so Space→miya-comms cannot invent inform_staff + fake confirmation cards.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import {
    collectUserTextsFromMessages,
    extractLastUserText,
} from "../utils/extractLastUserText";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";
import {
    classifyStaffEscalation,
    TELL_MANAGER_RE,
    type StaffRouteKind,
} from "../utils/staffEscalationRouting";

const CONFIRM_SEND_RE =
    /^(yes([,!]?\s*(send(\s+it)?|please)?)?|oui([,!]?\s*(envoie|envoyer|s['']il\s+te\s+pla[iî]t)?)?|send(\s+it)?|confirm(ed)?|ok([,!]?\s*send)?|نعم|أرسل|ارسل)\s*[.!]?$/i;

const CANCEL_SEND_RE =
    /^(no([,!]?\s*(cancel|thanks)?)?|non([,!]?\s*(annule|merci)?)?|cancel(led)?|never\s*mind|لا|ألغ)\s*[.!]?$/i;

function isManagerDashboardChannel(channel: string): boolean {
    return /luapop|dashboard|webchat|embed/i.test(channel || "");
}

function findPriorTellManagerText(messages: ChatMessage[]): string | null {
    for (const text of collectUserTextsFromMessages(messages)) {
        if (CONFIRM_SEND_RE.test(text) || CANCEL_SEND_RE.test(text)) continue;
        if (/please confirm|correct recipient|preparing to (let|inform)/i.test(text)) continue;
        if (classifyStaffEscalation(text) || TELL_MANAGER_RE.test(text)) {
            return text.trim();
        }
    }
    return null;
}

function resolveRoutedAsk(
    lastText: string,
    messages: ChatMessage[],
): { category: StaffRouteKind; subject: string; description: string } | null {
    const direct = classifyStaffEscalation(lastText);
    if (direct) {
        return { ...direct, description: lastText.trim() };
    }

    for (const text of collectUserTextsFromMessages(messages)) {
        const routed = classifyStaffEscalation(text);
        if (routed) {
            return { ...routed, description: text.trim() };
        }
    }

    if (CANCEL_SEND_RE.test(lastText.trim())) return null;

    if (CONFIRM_SEND_RE.test(lastText.trim())) {
        const prior = findPriorTellManagerText(messages);
        if (!prior) return null;
        const routed = classifyStaffEscalation(prior);
        if (!routed) {
            return { category: "OTHER", subject: prior.slice(0, 200), description: prior };
        }
        return { ...routed, description: prior };
    }

    return null;
}

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

function staffFacingSuccess(category: StaffRouteKind): string {
    if (category === "PAYROLL") {
        return "Thanks — I've passed your unpaid wages / payroll note on to your manager. They'll see it under *Human Resources* (Pending) and get back to you as soon as they can.";
    }
    if (category === "HR" || category === "DOCUMENT") {
        return "Thanks — I've passed that on to your manager. They'll see it under *Human Resources* and get back to you as soon as they can.";
    }
    return "Thanks — I've passed that on to your manager. They'll get back to you as soon as they can.";
}

export const staffRequestPreprocessor = new PreProcessor({
    name: "staff-request-router",
    description:
        "Routes tell-my-manager / wages / payslip asks to staff_request (not inform_staff).",
    // Above Operations (105); below ClockIn (200). Must win over Space inventing confirm cards.
    priority: 190,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        if (isManagerDashboardChannel(channel)) {
            return { action: "proceed" as const };
        }

        const lastText = extractLastUserText(messages);
        if (CANCEL_SEND_RE.test(lastText.trim())) {
            return {
                action: "block" as const,
                response: "Okay — I cancelled that. Nothing was sent to your manager.",
                metadata: { staff_request_status: "cancelled" },
            };
        }

        const routed = resolveRoutedAsk(lastText, messages);
        if (!routed) {
            return { action: "proceed" as const };
        }

        const tenant = await resolveTenantForUser(user);
        const phone = phoneFromUser(user) || tenant.phone || "";

        if (!tenant.restaurantId) {
            console.warn(
                `[StaffRequestPreprocessor] No restaurant; channel=${channel} text=${JSON.stringify(lastText.slice(0, 80))}`,
            );
            return {
                action: "block" as const,
                response:
                    "I couldn't link this to your workspace yet. Please message from your registered staff WhatsApp number, then try again.",
            };
        }

        console.log(
            `[StaffRequestPreprocessor] category=${routed.category} restaurant=${tenant.restaurantId} channel=${channel}`,
        );

        const api = new ApiService();
        const result = await api.createStaffRequestForAgent({
            restaurant_id: tenant.restaurantId,
            subject: routed.subject,
            description: routed.description,
            category: routed.category,
            priority: routed.category === "MAINTENANCE" ? "HIGH" : "MEDIUM",
            phone: phone || undefined,
            auto_assign: true,
            metadata: {
                source_context: "staff_request_preprocessor",
                channel,
            },
        });

        if (!result.success) {
            console.error("[StaffRequestPreprocessor] ingest failed:", result.error);
            return {
                action: "block" as const,
                response:
                    "I couldn't pass that to your manager just now. Please try again in a moment.",
                metadata: { staff_request_status: "error", error: result.error },
            };
        }

        const apiMsg =
            typeof (result as { message_for_staff?: string }).message_for_staff === "string"
                ? String((result as { message_for_staff?: string }).message_for_staff).trim()
                : "";

        return {
            action: "block" as const,
            response: apiMsg || staffFacingSuccess(routed.category),
            metadata: {
                staff_request_status: "success",
                staff_request_category: routed.category,
                record_id: result.id,
            },
        };
    },
});

export default staffRequestPreprocessor;
