/**
 * Detects staff clock-in intent and runs clock-in via ApiService so the LLM
 * cannot invent generic clock-in errors or ask for opening float first.
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
import {
    shouldForceStaffClockIn,
    shareLocationClockInMessage,
} from "../shared/clockInGuard";

const api = new ApiService();

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
    if (
        e.includes("invalid or missing phone") ||
        e.includes("missing staff") ||
        e.includes("staff not found")
    )
        return "no_phone";
    if (e.includes("unauthorized") || e.includes("agent key")) return "unauthorized";
    if (e.includes("already clocked in")) return "already_clocked_in";
    return "server_error";
}

export const clockInPreprocessor = new PreProcessor({
    name: "clock-in-router",
    description:
        "Detects staff clock-in intent (and recovers from wrong cash-float asks), runs staff_clock_in, blocks with backend message.",
    priority: 200,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText = extractLastUserText(messages);
        const { lat, lng } = coordsFromMessages(messages);
        const hasLocation = lat !== undefined && lng !== undefined;
        const msgs = messages as unknown as Array<Record<string, unknown>>;
        const clockInIntent = shouldForceStaffClockIn(lastText, msgs, hasLocation);
        if (!clockInIntent) return { action: "proceed" as const };

        const source = asPhoneSource(user);
        const phone = resolveStaffPhoneForByPhoneTools(source, null);
        const staffId = resolveStaffIdFromLuaUser(source);
        const isWeb = isWebDeliveryChannel(channel);

        console.log(
            `[ClockInPreprocessor] Running staff_clock_in; phone=${phone || "(from uid)"}, hasLocation=${hasLocation}, channel=${channel}`,
        );

        let toolResult: { status: string; code: string; message: string };
        try {
            if (!phone && !staffId) {
                toolResult = {
                    status: "error",
                    code: "no_phone",
                    message: "We couldn't find your account. Please contact your manager to be added.",
                };
            } else {
                const useStaffId = (isWeb && !!staffId) || (!phone && !!staffId);
                const result = await api.clockInByPhone({
                    ...(useStaffId ? { staff_id: staffId } : { phone }),
                    delivery_channel: channel || (isWeb ? "web" : "whatsapp"),
                    ...(hasLocation ? { latitude: lat, longitude: lng } : {}),
                });
                if (result.success) {
                    toolResult = {
                        status: "success",
                        code: (result as { already_clocked_in?: boolean }).already_clocked_in
                            ? "already_clocked_in"
                            : "clocked_in",
                        message: result.message_for_user || "You're clocked in! Have a great shift.",
                    };
                } else {
                    const code = mapErrorToCode(result.error);
                    const message =
                        result.message_for_user ||
                        result.error ||
                        "We couldn't record your clock-in. Please contact your manager.";
                    toolResult =
                        code === "location_required"
                            ? { status: "success", code: "location_required", message }
                            : { status: "error", code, message };
                }
            }
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[ClockInPreprocessor] staff_clock_in threw:", em);
            toolResult = {
                status: "error",
                code: "server_error",
                message:
                    "We couldn't reach the clock-in service right now. Please try again in a moment.",
            };
        }

        let response = String(toolResult.message || "").trim();
        const code = String(toolResult.code || "");
        if (!response) {
            response =
                code === "location_required"
                    ? shareLocationClockInMessage(channel)
                    : "We couldn't complete clock-in. Please try again or contact your manager.";
        }

        console.log(
            `[ClockInPreprocessor] Blocking with tool message (code=${code}, status=${toolResult.status})`,
        );
        return {
            action: "block" as const,
            response,
            metadata: { clock_in_code: code, clock_in_status: toolResult.status },
        };
    },
});

export default clockInPreprocessor;
