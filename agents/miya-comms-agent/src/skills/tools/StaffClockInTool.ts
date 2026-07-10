import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { resolveStaffPhoneForByPhoneTools } from "../../utils/resolveStaffPhoneFromLuaUser";
import {
    isWebDeliveryChannel,
    resolveStaffIdFromLuaUser,
} from "../../utils/resolveStaffIdFromLuaUser";

const optionalCoord = z.preprocess(
    (v) => (v === null || v === undefined || v === "" ? undefined : v),
    z.union([z.number(), z.string()]).optional(),
);

const locationAttachmentSchema = z
    .object({
        latitude: z.union([z.number(), z.string()]).optional(),
        longitude: z.union([z.number(), z.string()]).optional(),
        lat: z.union([z.number(), z.string()]).optional(),
        lng: z.union([z.number(), z.string()]).optional(),
        lon: z.union([z.number(), z.string()]).optional(),
        degreesLatitude: z.union([z.number(), z.string()]).optional(),
        degreesLongitude: z.union([z.number(), z.string()]).optional(),
    })
    .passthrough()
    .optional();

function peelFiniteNumber(v: unknown): number | undefined {
    if (v === null || v === undefined || v === "") return undefined;
    const n =
        typeof v === "number" ? v : Number(String(v).trim().replace(/\u2212/g, "-").replace(/−/g, "-"));
    return Number.isFinite(n) ? n : undefined;
}

function latLngFromInput(input: Record<string, unknown>): { lat?: number; lng?: number } {
    let lat = peelFiniteNumber(input.latitude);
    let lng = peelFiniteNumber(input.longitude);
    const loc = input.location;
    if ((lat === undefined || lng === undefined) && loc && typeof loc === "object" && !Array.isArray(loc)) {
        const o = loc as Record<string, unknown>;
        lat =
            lat ??
            peelFiniteNumber(o.latitude) ??
            peelFiniteNumber(o.lat) ??
            peelFiniteNumber(o.degreesLatitude);
        lng =
            lng ??
            peelFiniteNumber(o.longitude) ??
            peelFiniteNumber(o.lng) ??
            peelFiniteNumber(o.lon) ??
            peelFiniteNumber(o.degreesLongitude);
    }
    return { lat, lng };
}

/**
 * staff_clock_in — record a staff clock-in via the Mizan backend.
 * WhatsApp: phone + optional location. LuaPop/web: staff_id from signed-in user context.
 */
export default class StaffClockInTool implements LuaTool {
    name = "staff_clock_in";
    description =
        "Clock in a staff member. Use when staff say 'clock in', 'clock-in', 'I want to clock in', etc. " +
        "Pass phone from WhatsApp context; on LuaPop/web pass channel and rely on staff_id from context. " +
        "If they shared location, pass latitude/longitude. ALWAYS reply with the exact 'message' field returned — never refuse or redirect to another system.";

    inputSchema = z
        .object({
            phone: z
                .preprocess((v) => (v === undefined || v === null ? "" : String(v)), z.string())
                .describe("Sender phone if known; may be empty on web — tool resolves from context."),
            channel: z
                .string()
                .optional()
                .describe("Delivery channel from preprocessor (whatsapp, luapop, web, etc.)."),
            latitude: optionalCoord.describe("Latitude if the user shared their location"),
            longitude: optionalCoord.describe("Longitude if the user shared their location"),
            location: locationAttachmentSchema.describe("Nested location object from WhatsApp/Meta"),
        })
        .passthrough();

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    private mapErrorToCode(rawError: string | undefined | null): string {
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

    async execute(rawInput: unknown) {
        const parsed = this.inputSchema.safeParse(rawInput ?? {});
        if (!parsed.success) {
            console.error("[StaffClockInTool] Input validation failed:", parsed.error.flatten?.() ?? parsed.error);
            return {
                status: "error",
                code: "invalid_input",
                message:
                    "We couldn't read your location from this message. Please tap Share Location in WhatsApp, or open Time Clock in the staff app.",
            };
        }
        const input = parsed.data as z.infer<typeof this.inputSchema>;

        const user = await User.get();
        const userData = (user as any)?.data || {};
        const profile = (user as any)?._luaProfile || {};

        const hasAgentKey = !!(
            env("LUA_WEBHOOK_API_KEY") ||
            env("WEBHOOK_API_KEY") ||
            env("MIZAN_SERVICE_TOKEN")
        );

        if (!hasAgentKey) {
            console.error("[StaffClockInTool] No LUA_WEBHOOK_API_KEY / WEBHOOK_API_KEY / MIZAN_SERVICE_TOKEN in environment.");
            return {
                status: "error",
                code: "unauthorized",
                message:
                    "Clock-in is temporarily unavailable (the agent isn't authorised to talk to the backend). Please contact your manager.",
            };
        }

        const channel = String(input.channel || "").trim();
        const isWeb = isWebDeliveryChannel(channel);
        const phone = resolveStaffPhoneForByPhoneTools(
            user
                ? { uid: (user as any).uid, data: userData as Record<string, unknown>, _luaProfile: profile }
                : null,
            input.phone,
        );
        const staffId = user
            ? resolveStaffIdFromLuaUser({
                  uid: (user as any).uid,
                  data: userData as Record<string, unknown>,
                  _luaProfile: profile,
              })
            : "";

        if (!phone && !staffId) {
            console.error(
                `[StaffClockInTool] No valid phone or staff_id: uid=${user ? (user as any).uid : "(no User.get)"}`,
            );
            return {
                status: "error",
                code: "no_phone",
                message: "We couldn't find your account. Please contact your manager to be added.",
            };
        }

        try {
            const { lat: latN, lng: lngN } = latLngFromInput(input as Record<string, unknown>);

            const useStaffId = (isWeb && !!staffId) || (!phone && !!staffId);
            console.log(
                `[StaffClockInTool] clock-in (channel=${channel || "-"}, staff_id=${useStaffId ? staffId.slice(0, 8) + "…" : "-"}, phone=${phone ? "***" + phone.slice(-4) : "-"}, lat=${latN ?? "-"}, lng=${lngN ?? "-"})`,
            );

            const result = await this.apiService.clockInByPhone({
                ...(useStaffId ? { staff_id: staffId } : { phone }),
                delivery_channel: channel || (isWeb ? "web" : "whatsapp"),
                latitude: latN,
                longitude: lngN,
            });

            console.log(
                `[StaffClockInTool] Result: success=${result.success}, error=${result.error || "-"}, message_for_user="${(result.message_for_user || "").slice(0, 120)}"`,
            );

            if (result.success) {
                return {
                    status: "success",
                    code: (result as any).already_clocked_in ? "already_clocked_in" : "clocked_in",
                    message: result.message_for_user || "You're clocked in! Have a great shift.",
                    miya_directive: "Relay the message field VERBATIM. Do NOT paraphrase or apologize.",
                };
            }

            const code = this.mapErrorToCode(result.error);
            const message =
                result.message_for_user ||
                result.error ||
                "We couldn't record your clock-in. Please contact your manager.";

            if (code === "location_required") {
                return {
                    status: "success",
                    code: "location_required",
                    message,
                    miya_directive:
                        "This is the expected next step — NOT an error. Relay the message field VERBATIM. " +
                        "Do NOT say 'there was an error' or redirect to another time-tracking system.",
                };
            }

            return {
                status: "error",
                code,
                message,
                miya_directive: "Relay the message field VERBATIM to the staff.",
            };
        } catch (error: any) {
            const em = String(error?.message || error || "");
            if (/Buffer|ArrayBuffer|first argument must be of type string/i.test(em)) {
                console.error("[StaffClockInTool] Transport/encoding error (masked for user):", em.slice(0, 200));
                return {
                    status: "error",
                    code: "network_error",
                    message:
                        "We couldn't finish processing your clock-in. Please try again in a moment, or contact your manager.",
                };
            }
            console.error(
                `[StaffClockInTool] Unexpected error: ${error?.message || error}`,
                error?.response?.data ? JSON.stringify(error.response.data).slice(0, 300) : "",
            );
            const apiBody = error?.response?.data || {};
            return {
                status: "error",
                code: error?.response ? "server_error" : "network_error",
                message:
                    (apiBody.message_for_user as string) ||
                    "We couldn't reach the clock-in service right now. Please try again in a minute, or contact your manager.",
            };
        }
    }
}
