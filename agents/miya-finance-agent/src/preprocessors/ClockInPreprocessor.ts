/**
 * Detects staff clock-in intent and runs clock-in via ApiService so the LLM
 * cannot invent generic clock-in errors. (Checklist start lives on miya-ops.)
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import { extractLastUserText } from "../utils/extractLastUserText";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";
import {
    isWebDeliveryChannel,
    resolveStaffIdFromLuaUser,
} from "../utils/resolveStaffIdFromLuaUser";

const CLOCK_IN_RE =
    /\b(clock[\s-]?in|clockin|pointer|pointage|start my shift|i['']?m here|arriver|سجل دخول|بغيت نبدا|بغيت نبدا الخدمة|nbeda lkhedma)\b/i;

const api = new ApiService();

function isClockInMessage(text: string): boolean {
    const lower = text.toLowerCase().trim();
    if (!lower) return false;
    if (CLOCK_IN_RE.test(lower)) return true;
    if (lower.includes("want to clock in")) return true;
    if (lower.includes("hi miya") && lower.includes("clock")) return true;
    return false;
}

function asPhoneSource(user: UserDataInstance): LuaUserPhoneSource & { uid?: string } {
    const u = user as unknown as LuaUserPhoneSource & {
        uid?: string;
        data?: Record<string, unknown>;
        _luaProfile?: Record<string, unknown>;
    };
    return { uid: u.uid, data: u.data, _luaProfile: u._luaProfile };
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
                peelCoord(m.latitude) ?? peelCoord(m.lat) ?? peelCoord(m.degreesLatitude);
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

function mapErrorToCode(rawError: string | undefined | null): string {
    const e = String(rawError || "").toLowerCase();
    if (!e) return "server_error";
    if (e.includes("location required")) return "location_required";
    if (e.includes("outside geofence")) return "outside_geofence";
    if (e.includes("geofence not configured")) return "no_geofence";
    if (e.includes("no shift") || e.includes("no scheduled shift")) return "no_shift";
    if (e.includes("invalid coordinates")) return "invalid_coordinates";
    if (e.includes("invalid or missing phone") || e.includes("missing staff") || e.includes("staff not found"))
        return "no_phone";
    if (e.includes("unauthorized") || e.includes("agent key")) return "unauthorized";
    if (e.includes("already clocked in")) return "already_clocked_in";
    return "server_error";
}

export const clockInPreprocessor = new PreProcessor({
    name: "clock-in-router",
    description:
        "Detects staff clock-in intent, runs staff_clock_in, and injects the backend message for verbatim relay.",
    priority: 1,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText = extractLastUserText(messages);
        const { lat, lng } = coordsFromMessages(messages);
        const hasLocation = lat !== undefined && lng !== undefined;
        const clockInIntent = isClockInMessage(lastText) || (hasLocation && !lastText.trim());
        if (!clockInIntent) return { action: "proceed" as const };

        const source = asPhoneSource(user);
        const phone = resolveStaffPhoneForByPhoneTools(source, null);
        const staffId = resolveStaffIdFromLuaUser(source);
        const isWeb = isWebDeliveryChannel(channel);

        console.log(
            `[ClockInPreprocessor] Running staff_clock_in; phone=${phone || "(from uid)"}, hasLocation=${hasLocation}, channel=${channel}`,
        );

        let response = "";
        let code = "server_error";
        let status = "error";

        try {
            if (!phone && !staffId) {
                response = "We couldn't find your account. Please contact your manager to be added.";
                code = "no_phone";
            } else {
                const useStaffId = (isWeb && !!staffId) || (!phone && !!staffId);
                const result = await api.clockInByPhone({
                    ...(useStaffId ? { staff_id: staffId } : { phone }),
                    delivery_channel: channel || (isWeb ? "web" : "whatsapp"),
                    ...(hasLocation ? { latitude: lat, longitude: lng } : {}),
                });
                if (result.success) {
                    status = "success";
                    code = (result as { already_clocked_in?: boolean }).already_clocked_in
                        ? "already_clocked_in"
                        : "clocked_in";
                    response = result.message_for_user || "You're clocked in! Have a great shift.";
                } else {
                    code = mapErrorToCode(result.error);
                    response =
                        result.message_for_user ||
                        result.error ||
                        "We couldn't record your clock-in. Please contact your manager.";
                    if (code === "location_required") status = "success";
                }
            }
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[ClockInPreprocessor] staff_clock_in threw:", em);
            response =
                "We couldn't reach the clock-in service right now. Please try again in a moment.";
        }

        if (!response) {
            response =
                code === "location_required"
                    ? "Share your live location to clock in."
                    : "We couldn't complete clock-in. Please try again or contact your manager.";
        }

        return {
            action: "block" as const,
            response,
            metadata: { clock_in_code: code, clock_in_status: status },
        };
    },
});

export default clockInPreprocessor;
