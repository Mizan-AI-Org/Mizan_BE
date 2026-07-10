/**
 * Detects staff clock-out intent and runs staff_clock_out so the LLM cannot skip it.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import StaffClockOutTool from "../skills/tools/StaffClockOutTool";
import { extractLastUserText } from "../utils/extractLastUserText";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";

const CLOCK_OUT_RE =
    /\b(clock[\s-]?out|clockout|end\s+my\s+shift|i['']?m\s+done|finir\s+(mon\s+)?service|pointer\s+sortie|سجل\s*خروج|بغيت\s*نخرج|nsali)\b/i;

const clockOutTool = new StaffClockOutTool();

function isClockOutMessage(text: string): boolean {
    const lower = text.toLowerCase().trim();
    if (!lower) return false;
    if (CLOCK_OUT_RE.test(lower)) return true;
    if (lower.includes("want to clock out")) return true;
    return false;
}

function resolvePhone(user: UserDataInstance): string {
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

export const clockOutPreprocessor = new PreProcessor({
    name: "clock-out-router",
    description: "Detects staff clock-out intent, runs staff_clock_out, blocks with backend message.",
    priority: 8,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText = extractLastUserText(messages);
        if (!isClockOutMessage(lastText)) {
            return { action: "proceed" as const };
        }

        const phone = resolvePhone(user);
        console.log(
            `[ClockOutPreprocessor] Running staff_clock_out; phone=${phone || "(from uid)"}, channel=${channel}`,
        );

        let toolResult: Record<string, unknown> = {};
        try {
            toolResult = (await clockOutTool.execute({
                phone: phone || "",
            })) as Record<string, unknown>;
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[ClockOutPreprocessor] staff_clock_out threw:", em);
            toolResult = {
                status: "error",
                message: "We couldn't reach the clock-out service right now. Please try again in a moment.",
            };
        }

        const message = String(toolResult.message || "").trim();
        if (message) {
            return {
                action: "block" as const,
                response: message,
                metadata: { clock_out_status: toolResult.status },
            };
        }

        return { action: "proceed" as const };
    },
});

export default clockOutPreprocessor;
