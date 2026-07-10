/**
 * Routes staff "tell my manager / unpaid wages / payslip" through staff_request
 * so Space→miya-comms cannot invent inform_staff + fake confirmation cards.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import { extractLastUserText, extractMessageText } from "../utils/extractLastUserText";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";

type StaffRouteKind =
    | "PAYROLL"
    | "DOCUMENT"
    | "HR"
    | "SCHEDULING"
    | "MAINTENANCE"
    | "OTHER";

const TELL_MANAGER_RE =
    /\b(tell\s+(my\s+)?manager|pass\s+(this\s+)?(to|on\s+to)\s+(my\s+)?manager|let\s+(my\s+)?manager\s+know|inform\s+(my\s+)?manager|dis\s+[àa]\s+(mon\s+)?(manager|responsable|patron)|قل\s+(ل|لـ)?(المدير|المانجر|المسؤول))\b/i;

const PAYROLL_RE =
    /\b(pay\s*slip|payslip|pay\s*stub|salary\s+slip|bulletin\s+de\s+paie|fiche\s+de\s+paie|كشف\s+الراتب|ورقة\s+الأجر|my\s+pay|last\s+\d+\s+months?\s+pay|wages?|salary|unpaid\s+(pay|wages?|salary)|missing\s+(pay|wages?|salary)|haven['']?t\s+received\s+(my\s+)?(pay|wages?|salary|last)|yet\s+to\s+receive\s+(my\s+)?(pay|wages?|salary|last)|didn['']?t\s+(get|receive)\s+(my\s+)?(pay|wages?|salary)|last\s+week['']?s?\s+wages?|paie|salaire|أجرى|راتبي)\b/i;

const DOCUMENT_RE =
    /\b(visa|passport|work\s+permit|certificate|attestation|document|papers|وثيقة|تأشيرة|شهادة)\b/i;

const HR_RE =
    /\b(leave\s+request|time\s+off|vacation|holiday|sick\s+day|hr\s+request|cong[eé]|arrêt\s+maladie|إجازة)\b/i;

const SCHEDULING_RE =
    /\b(swap\s+(my\s+)?shift|change\s+(my\s+)?shift|cover\s+(my\s+)?shift|schedule\s+change|تبديل\s+الشيفت)\b/i;

const MAINTENANCE_RE =
    /\b(leak|not\s+working|repair|fix\s+the|maintenance|en\s+panne|fuite|خاسر|معطل|(?:broken|down)\s+(?:fridge|freezer|oven|dishwasher|ac|equipment|machine))\b/i;

const CONFIRM_SEND_RE =
    /^(yes([,!]?\s*(send(\s+it)?|please)?)?|oui([,!]?\s*(envoie|envoyer|s['']il\s+te\s+pla[iî]t)?)?|send(\s+it)?|confirm(ed)?|ok([,!]?\s*send)?|نعم|أرسل|ارسل)\s*[.!]?$/i;

const CANCEL_SEND_RE =
    /^(no([,!]?\s*(cancel|thanks)?)?|non([,!]?\s*(annule|merci)?)?|cancel(led)?|never\s*mind|لا|ألغ)\s*[.!]?$/i;

function classifyStaffAsk(text: string): { category: StaffRouteKind; subject: string } | null {
    const t = text.trim();
    if (!t || t.length < 8) return null;

    const wantsManager = TELL_MANAGER_RE.test(t);
    const isPayroll = PAYROLL_RE.test(t);
    const isDoc = DOCUMENT_RE.test(t);
    const isHr = HR_RE.test(t);
    const isSched = SCHEDULING_RE.test(t);
    const isMaint = MAINTENANCE_RE.test(t);

    if (wantsManager) {
        if (isPayroll) return { category: "PAYROLL", subject: t.slice(0, 200) };
        if (isDoc) return { category: "DOCUMENT", subject: t.slice(0, 200) };
        if (isHr) return { category: "HR", subject: t.slice(0, 200) };
        if (isSched) return { category: "SCHEDULING", subject: t.slice(0, 200) };
        if (isMaint) return { category: "MAINTENANCE", subject: t.slice(0, 200) };
        return { category: "OTHER", subject: t.slice(0, 200) };
    }

    if (
        isPayroll &&
        /\b(need|want|ask|request|can\s+i|please|haven['']?t|yet\s+to|didn['']?t|missing|unpaid|بغيت|خاصني|je\s+veux|j['']ai\s+besoin)\b/i.test(
            t,
        )
    ) {
        return { category: "PAYROLL", subject: t.slice(0, 200) };
    }
    if (isDoc && /\b(need|want|apply|request|please|بغيت|خاصني|je\s+veux|demande)\b/i.test(t)) {
        return { category: "DOCUMENT", subject: t.slice(0, 200) };
    }

    return null;
}

function findPriorTellManagerText(messages: ChatMessage[]): string | null {
    for (let i = messages.length - 1; i >= 0; i--) {
        const text = extractMessageText(messages[i] as ChatMessage);
        if (!text) continue;
        if (CONFIRM_SEND_RE.test(text) || CANCEL_SEND_RE.test(text)) continue;
        if (/please confirm|correct recipient|preparing to (let|inform)/i.test(text)) continue;
        if (classifyStaffAsk(text) || TELL_MANAGER_RE.test(text)) {
            return text.trim();
        }
    }
    return null;
}

function resolveRoutedAsk(
    lastText: string,
    messages: ChatMessage[],
): { category: StaffRouteKind; subject: string; description: string } | null {
    const direct = classifyStaffAsk(lastText);
    if (direct) {
        return { ...direct, description: lastText.trim() };
    }

    if (CANCEL_SEND_RE.test(lastText.trim())) return null;

    if (CONFIRM_SEND_RE.test(lastText.trim())) {
        const prior = findPriorTellManagerText(messages);
        if (!prior) return null;
        const routed = classifyStaffAsk(prior);
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
        return "Thanks — I've passed your unpaid wages / payroll note on to your manager. They'll get back to you as soon as they can.";
    }
    return "Thanks — I've passed that on to your manager. They'll get back to you as soon as they can.";
}

export const staffRequestPreprocessor = new PreProcessor({
    name: "staff-request-router",
    description:
        "Routes tell-my-manager / wages / payslip asks to staff_request (not inform_staff).",
    // Ahead of LLM so "tell my manager" never becomes a fake WhatsApp ping confirm.
    priority: 100,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        if (channel && !/whatsapp/i.test(channel)) {
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
