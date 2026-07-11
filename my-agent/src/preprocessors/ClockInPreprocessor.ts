/**
 * Detects staff clock-in intent and runs staff_clock_in in the preprocessor so the
 * LLM cannot skip the tool, ask for opening float, or invent generic clock-in errors.
 *
 * Calls ApiService directly (not only via StaffClockInTool) so we never depend on
 * User.get() being available inside the preprocessor runtime.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import ChecklistStarterTool from "../skills/tools/ChecklistStarterTool";
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

const checklistStarterTool = new ChecklistStarterTool();
const api = new ApiService();

function asPhoneSource(user: UserDataInstance): LuaUserPhoneSource & { uid?: string } {
    const u = user as unknown as LuaUserPhoneSource & {
        uid?: string;
        data?: Record<string, unknown>;
        _luaProfile?: Record<string, unknown>;
    };
    return {
        uid: u.uid,
        data: u.data,
        _luaProfile: u._luaProfile,
    };
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

function mapErrorToCode(rawError: string | undefined | null): string {
    const e = String(rawError || "").toLowerCase();
    if (!e) return "server_error";
    if (e.includes("location required")) return "location_required";
    if (e.includes("outside geofence")) return "outside_geofence";
    if (e.includes("geofence not configured")) return "no_geofence";
    if (e.includes("no shift") || e.includes("no scheduled shift")) return "no_shift";
    if (e.includes("invalid coordinates")) return "invalid_coordinates";
    if (e.includes("invalid or missing phone") || e.includes("missing staff")) return "no_phone";
    if (e.includes("staff not found")) return "no_phone";
    if (e.includes("unauthorized") || e.includes("agent key")) return "unauthorized";
    if (e.includes("already clocked in")) return "already_clocked_in";
    return "server_error";
}

async function runClockIn(
    user: UserDataInstance,
    channel: string,
    coords: { lat?: number; lng?: number },
): Promise<{ status: string; code: string; message: string }> {
    const source = asPhoneSource(user);
    const phone = resolveStaffPhoneForByPhoneTools(source, null);
    const staffId = resolveStaffIdFromLuaUser(source);
    const isWeb = isWebDeliveryChannel(channel);
    const hasLocation = coords.lat !== undefined && coords.lng !== undefined;

    if (!phone && !staffId) {
        return {
            status: "error",
            code: "no_phone",
            message: "We couldn't find your account. Please contact your manager to be added.",
        };
    }

    const useStaffId = (isWeb && !!staffId) || (!phone && !!staffId);
    console.log(
        `[ClockInPreprocessor] API clock-in channel=${channel || "-"} phone=${phone ? "***" + phone.slice(-4) : "-"} staff=${useStaffId ? "yes" : "no"} loc=${hasLocation}`,
    );

    const result = await api.clockInByPhone({
        ...(useStaffId ? { staff_id: staffId } : { phone }),
        delivery_channel: channel || (isWeb ? "web" : "whatsapp"),
        ...(hasLocation ? { latitude: coords.lat, longitude: coords.lng } : {}),
    });

    if (result.success) {
        return {
            status: "success",
            code: (result as { already_clocked_in?: boolean }).already_clocked_in
                ? "already_clocked_in"
                : "clocked_in",
            message: result.message_for_user || "You're clocked in! Have a great shift.",
        };
    }

    const code = mapErrorToCode(result.error);
    const message =
        result.message_for_user ||
        result.error ||
        "We couldn't record your clock-in. Please contact your manager.";

    if (code === "location_required") {
        return { status: "success", code: "location_required", message };
    }
    return { status: "error", code, message };
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

        if (!clockInIntent) {
            return { action: "proceed" as const };
        }

        // WhatsApp without GPS: never call the API or invent failures — ask for location.
        if (!hasLocation && !isWebDeliveryChannel(channel)) {
            return {
                action: "block" as const,
                response: shareLocationClockInMessage(channel),
                metadata: { clock_in_code: "location_required", clock_in_status: "success" },
            };
        }

        const phone = resolveStaffPhoneForByPhoneTools(asPhoneSource(user), null);
        console.log(
            `[ClockInPreprocessor] Running staff_clock_in; phone=${phone || "(from uid)"}, hasLocation=${hasLocation}, channel=${channel}, lastText=${JSON.stringify(lastText.slice(0, 80))}`,
        );

        let toolResult: { status: string; code: string; message: string };
        try {
            toolResult = await runClockIn(user, channel, { lat, lng });
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
        const status = String(toolResult.status || "");

        if (!response) {
            response =
                code === "location_required"
                    ? shareLocationClockInMessage(channel)
                    : "We couldn't complete clock-in. Please try again or contact your manager.";
        }

        if (code === "clocked_in") {
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
                    (cStatus === "started" ||
                        cStatus === "in_progress" ||
                        cStatus === "next_task" ||
                        cStatus === "no_checklists" ||
                        cStatus === "completed")
                ) {
                    response = `${response}\n\n${checklistMsg}`;
                }
            } catch (err: unknown) {
                const em = err instanceof Error ? err.message : String(err);
                console.warn("[ClockInPreprocessor] checklist_starter after clock-in failed:", em);
            }
        }

        console.log(
            `[ClockInPreprocessor] Blocking with tool message (code=${code}, status=${status})`,
        );
        return {
            action: "block" as const,
            response,
            metadata: { clock_in_code: code, clock_in_status: status },
        };
    },
});

export default clockInPreprocessor;
