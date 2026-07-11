/**
 * Deterministic "when is my shift" router — calls getMyShiftsForAgent so Space/LLM
 * cannot invent "trouble fetching your shift details".
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import { extractLastUserText } from "../utils/extractLastUserText";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";
import { resolveStaffIdFromLuaUser } from "../utils/resolveStaffIdFromLuaUser";

const MY_SHIFTS_RE =
    /\b(my\s+shifts?|my\s+schedule|when\s+(?:is|are)\s+my\s+shifts?|what(?:'s|\s+is|\s+are)\s+my\s+(?:shift|schedule)|shifts?\s+(?:today|tomorrow)|schedule\s+(?:today|tomorrow)|do\s+i\s+(?:work|have\s+(?:a\s+)?shift)|am\s+i\s+(?:working|scheduled)|horaire|mes\s+shifts?|mon\s+planning|شيفت|دوامي|جدول)\b/i;

function isMyShiftsAsk(text: string): boolean {
    const t = text.trim();
    return Boolean(t && t.length >= 5 && MY_SHIFTS_RE.test(t));
}

function parseRange(text: string): { start_date: string; end_date: string } {
    const today = new Date();
    const iso = (d: Date) => d.toISOString().slice(0, 10);
    const lower = text.toLowerCase();
    const addDays = (n: number) => {
        const d = new Date(today);
        d.setDate(d.getDate() + n);
        return d;
    };
    if (/\btoday\b/.test(lower) && /\btomorrow\b/.test(lower)) {
        return { start_date: iso(today), end_date: iso(addDays(1)) };
    }
    if (/\btomorrow\b/.test(lower) && !/\btoday\b/.test(lower)) {
        const d = addDays(1);
        return { start_date: iso(d), end_date: iso(d) };
    }
    if (/\btoday\b|\btonight\b/.test(lower)) {
        return { start_date: iso(today), end_date: iso(today) };
    }
    return { start_date: iso(today), end_date: iso(addDays(1)) };
}

function formatShiftsReply(
    firstName: string,
    shifts: Array<Record<string, unknown>>,
    rangeLabel: string,
): string {
    const name = firstName.trim() || "there";
    if (!shifts.length) {
        return `Hi ${name} — you have no shifts scheduled for *${rangeLabel}*.`;
    }
    const lines = [`Hi ${name} — here are your shifts:`, ""];
    for (const s of shifts) {
        const day = String(s.shift_date || "—");
        const start = String(s.start_time || "—");
        const end = String(s.end_time || "—");
        const role = String(s.role || "").trim();
        const roleBit = role ? ` (${role})` : "";
        lines.push(`• *${day}* ${start}–${end}${roleBit}`);
    }
    lines.push("");
    lines.push("Say *Clock me in* when you're at work and I'll ask for your location.");
    return lines.join("\n");
}

export const myShiftsPreprocessor = new PreProcessor({
    name: "my-shifts-router",
    description: "Answers staff 'when is my shift' asks via the scheduling API.",
    // Below ClockIn (200) / StaffRequest (190); above Operations (105).
    priority: 175,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText = extractLastUserText(messages);
        if (!isMyShiftsAsk(lastText)) {
            return { action: "proceed" as const };
        }

        const u = user as unknown as LuaUserPhoneSource & {
            uid?: string;
            data?: Record<string, unknown>;
            _luaProfile?: Record<string, unknown>;
        };
        const phone = resolveStaffPhoneForByPhoneTools(
            {
                uid: u.uid,
                data: (u as { data?: Record<string, unknown> }).data,
                _luaProfile: (u as { _luaProfile?: Record<string, unknown> })._luaProfile,
            },
            null,
        );
        const staffId = resolveStaffIdFromLuaUser({
            uid: u.uid,
            data: (u as { data?: Record<string, unknown> }).data,
            _luaProfile: (u as { _luaProfile?: Record<string, unknown> })._luaProfile,
        });

        if (!phone && !staffId) {
            return {
                action: "block" as const,
                response:
                    "I couldn't link this chat to your staff profile yet. Please message from your registered WhatsApp number, then ask again.",
            };
        }

        const range = parseRange(lastText);
        const rangeLabel =
            range.start_date === range.end_date
                ? range.start_date
                : `${range.start_date} → ${range.end_date}`;

        console.log(
            `[MyShiftsPreprocessor] channel=${channel} phone=${phone ? "***" + phone.slice(-4) : "-"} staff=${staffId || "-"} range=${rangeLabel}`,
        );

        const api = new ApiService();
        try {
            const result = await api.getMyShiftsForAgent({
                ...(staffId ? { staff_id: staffId } : { phone }),
                start_date: range.start_date,
                end_date: range.end_date,
            });

            if (!result.success) {
                console.error("[MyShiftsPreprocessor] API failed:", result.error);
                return {
                    action: "block" as const,
                    response:
                        "I couldn't load your shifts just now. Please try again in a moment, or ask your manager to check the schedule.",
                };
            }

            const firstName = String(result.staff?.first_name || "").trim();
            const reply = formatShiftsReply(
                firstName,
                (result.shifts || []) as Array<Record<string, unknown>>,
                rangeLabel,
            );
            return {
                action: "block" as const,
                response: reply,
                metadata: { my_shifts_count: result.shifts?.length ?? 0 },
            };
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[MyShiftsPreprocessor] threw:", em);
            return {
                action: "block" as const,
                response:
                    "I couldn't load your shifts just now. Please try again in a moment, or ask your manager to check the schedule.",
            };
        }
    },
});

export default myShiftsPreprocessor;
