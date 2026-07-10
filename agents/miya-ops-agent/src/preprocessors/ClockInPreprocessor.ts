/**
 * Detects staff clock-in intent and runs staff_clock_in in the preprocessor so the
 * LLM cannot skip the tool or invent generic clock-in errors.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import StaffClockInTool from "../skills/tools/StaffClockInTool";
import ChecklistStarterTool from "../skills/tools/ChecklistStarterTool";
import { extractLastUserText } from "../utils/extractLastUserText";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";

const CLOCK_IN_RE =
    /\b(clock[\s-]?in|clockin|pointer|pointage|start my shift|i['']?m here|arriver|سجل دخول|بغيت نبدا|بغيت نبدا الخدمة|nbeda lkhedma)\b/i;

const clockInTool = new StaffClockInTool();
const checklistStarterTool = new ChecklistStarterTool();

function isClockInMessage(text: string): boolean {
    const lower = text.toLowerCase().trim();
    if (!lower) return false;
    if (CLOCK_IN_RE.test(lower)) return true;
    if (lower.includes("want to clock in")) return true;
    if (lower.includes("hi miya") && lower.includes("clock")) return true;
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

function peelCoord(v: unknown): number | undefined {
    if (v === null || v === undefined || v === "") return undefined;
    const n =
        typeof v === "number" ? v : Number(String(v).trim().replace(/\u2212/g, "-").replace(/−/g, "-"));
    return Number.isFinite(n) ? n : undefined;
}

function coordsFromMessages(messages: ChatMessage[]): { lat?: number; lng?: number } {
    for (const msg of messages) {
        const m = msg as unknown as Record<string, unknown>;
        if (m.type === "location" || m.latitude != null || m.longitude != null) {
            const lat =
                peelCoord(m.latitude) ??
                peelCoord(m.lat) ??
                peelCoord(m.degreesLatitude);
            const lng =
                peelCoord(m.longitude) ??
                peelCoord(m.lng) ??
                peelCoord(m.lon) ??
                peelCoord(m.degreesLongitude);
            if (lat !== undefined && lng !== undefined) return { lat, lng };
        }
    }
    return {};
}

export const clockInPreprocessor = new PreProcessor({
    name: "clock-in-router",
    description:
        "Detects staff clock-in intent, runs staff_clock_in, and injects the backend message for verbatim relay.",
    priority: 8,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText = extractLastUserText(messages);
        const { lat, lng } = coordsFromMessages(messages);
        const hasLocation = lat !== undefined && lng !== undefined;
        // Location alone only counts as clock-in when there is no other text
        // (Share Location reply) — avoid treating random GPS shares as clock-in.
        const clockInIntent =
            isClockInMessage(lastText) || (hasLocation && !lastText.trim());

        if (!clockInIntent) {
            return { action: "proceed" as const };
        }

        const phone = resolvePhone(user);
        console.log(
            `[ClockInPreprocessor] Running staff_clock_in; phone=${phone || "(from uid)"}, hasLocation=${hasLocation}, channel=${channel}, lastText=${JSON.stringify(lastText.slice(0, 80))}`,
        );

        let toolResult: Record<string, unknown> = {};
        try {
            toolResult = (await clockInTool.execute({
                phone: phone || "",
                channel,
                ...(hasLocation ? { latitude: lat, longitude: lng } : {}),
            })) as Record<string, unknown>;
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[ClockInPreprocessor] staff_clock_in threw:", em);
            toolResult = {
                status: "error",
                code: "server_error",
                message: "We couldn't reach the clock-in service right now. Please try again in a moment.",
            };
        }

        const message = String(toolResult.message || "").trim();
        const code = String(toolResult.code || "");
        const status = String(toolResult.status || "");

        // After a successful clock-in, start the checklist in the same turn so
        // staff get one natural reply (clock-in + first task) without relying on
        // the LLM or a second Django WhatsApp blast.
        let response = message;
        if (message && code === "clocked_in") {
            try {
                const checklistResult = (await checklistStarterTool.execute({
                    mode: "start",
                    trigger: "clock_in",
                    phone: phone || undefined,
                })) as Record<string, unknown>;
                const checklistMsg = String(checklistResult.message || "").trim();
                const cStatus = String(checklistResult.status || "");
                if (
                    checklistMsg &&
                    (cStatus === "started" || cStatus === "in_progress" || cStatus === "next_task")
                ) {
                    response = `${message}\n\n${checklistMsg}`;
                } else if (checklistMsg && cStatus === "not_clocked_in") {
                    // Shouldn't happen right after clock-in; keep clock-in only
                } else if (checklistMsg && cStatus === "no_checklists") {
                    response = `${message}\n\n${checklistMsg}`;
                } else if (cStatus === "completed" && checklistMsg) {
                    response = `${message}\n\n${checklistMsg}`;
                }
            } catch (err: unknown) {
                const em = err instanceof Error ? err.message : String(err);
                console.warn("[ClockInPreprocessor] checklist_starter after clock-in failed:", em);
            }
        }

        if (response) {
            console.log(
                `[ClockInPreprocessor] Blocking with tool message (code=${code}, status=${status})`,
            );
            return {
                action: "block" as const,
                response,
                metadata: { clock_in_code: code, clock_in_status: status },
            };
        }

        const block = `[CLOCK-IN TOOL ALREADY EXECUTED — REPLY WITH THIS TEXT ONLY]
status=${status}
code=${code}
message=${JSON.stringify(message)}

Your ENTIRE reply to the staff must be EXACTLY the message string above — character for character, no preface, no apology, no "I am processing".
FORBIDDEN: "there was an error", "I am unable to clock you in", "I am processing your clock-in request", "contact support", "designated time-tracking system", "I cannot directly perform".
${code === "location_required" ? "location_required is SUCCESS — Share Location button was sent by the backend (WhatsApp) or user must open Time Clock on web." : ""}`;

        const modifiedMessages = messages.map((m) =>
            m.type === "text" ? { ...m, text: `${block}\n\n${m.text}` } : m,
        );

        return { action: "proceed" as const, modifiedMessage: modifiedMessages };
    },
});

export default clockInPreprocessor;
