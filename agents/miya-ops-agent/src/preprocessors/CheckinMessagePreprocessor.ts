/**
 * Intercepts late/absence free-text ("I'll be late", "stuck in traffic", "malade", "retard")
 * and calls classifyCheckinMessageForAgent BEFORE falling through to the LLM —
 * same pattern as ClockInPreprocessor.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import { extractLastUserText } from "../utils/extractLastUserText";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";

/** Aligns with dashboard.views_ops_memory.agent_classify_checkin_message labels. */
const CHECKIN_FREE_TEXT_RE =
    /\b(i['']?ll\s+be\s+late|running\s+late|be\s+late|late\s+today|stuck\s+in\s+traffic|traffic|embouteillage|retard|en\s+retard|je\s+(vais\s+)?(être|etre)\s+en\s+retard|غادي\s*نتاخر|ghadi\s*ntakher|sick|malade|مريض|can['']?t\s+come|cannot\s+come|ne\s+peux\s+pas|absent|absence|won['']?t\s+make\s+it|leave\s+early|partir\s+t[oô]t|غادي\s*نمشي)\b/i;

const api = new ApiService();

function isCheckinFreeText(text: string): boolean {
    const lower = text.toLowerCase().trim();
    if (!lower || lower.length < 3) return false;
    return CHECKIN_FREE_TEXT_RE.test(lower);
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

export const checkinMessagePreprocessor = new PreProcessor({
    name: "checkin-message-router",
    description:
        "Detects late/absence free-text, classifies via classifyCheckinMessageForAgent, blocks with backend message.",
    /** Below clock-in (95) so GPS clock-in wins when both could match. */
    priority: 92,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText = extractLastUserText(messages);
        if (!isCheckinFreeText(lastText)) {
            return { action: "proceed" as const };
        }

        const tenant = await resolveTenantForUser(user);
        const restaurantId = tenant.restaurantId;
        const phone = tenant.phone || phoneFromUser(user);

        if (!restaurantId) {
            console.warn(
                `[CheckinMessagePreprocessor] No restaurantId; falling through to LLM. channel=${channel}`,
            );
            return { action: "proceed" as const };
        }

        console.log(
            `[CheckinMessagePreprocessor] Classifying check-in free-text; phone=${phone ? "***" + phone.slice(-4) : "-"}, channel=${channel}, text=${JSON.stringify(lastText.slice(0, 80))}`,
        );

        let result: {
            success?: boolean;
            classification?: string;
            note_id?: string;
            task_id?: string;
            message?: string;
            error?: string;
        };
        try {
            result = await api.classifyCheckinMessageForAgent(
                restaurantId,
                { text: lastText, sender_phone: phone || undefined },
                (user.data as { token?: string } | undefined)?.token || null,
            );
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[CheckinMessagePreprocessor] classify threw:", em);
            return { action: "proceed" as const };
        }

        if (!result.success) {
            console.warn(
                `[CheckinMessagePreprocessor] classify failed: ${result.error || "unknown"}; falling through`,
            );
            return { action: "proceed" as const };
        }

        const response = String(
            result.message ||
                `Logged as ${(result.classification || "note").replace(/_/g, " ")} against your profile.`,
        ).trim();

        console.log(
            `[CheckinMessagePreprocessor] Blocking with classification=${result.classification}`,
        );
        return {
            action: "block" as const,
            response,
            metadata: {
                checkin_classification: result.classification,
                checkin_note_id: result.note_id,
                checkin_task_id: result.task_id,
            },
        };
    },
});

export default checkinMessagePreprocessor;
