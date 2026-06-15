import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { resolveStaffPhoneForByPhoneTools } from "../../utils/resolveStaffPhoneFromLuaUser";

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
 * In production, **all staff clock in through WhatsApp**; many hits are recorded
 * directly when they share live location in WhatsApp. This tool is for when
 * Miya must record on their behalf (typed intent, pasted coordinates, etc.).
 *
 * The backend already produces precise, branded messages for every
 * outcome (location missing, geofence outside, already clocked in, server
 * error). A scheduled shift is optional — staff may clock in without one.
 * This tool never composes its own user text — it always passes the API's
 * `message_for_user` through and attaches a stable `code` so the persona
 * can rely on the exact shape when relaying to the user.
 *
 * Possible `code` values returned to Miya:
 *   - "clocked_in"          — fresh clock-in recorded
 *   - "already_clocked_in"  — idempotent acknowledgement
 *   - "location_required"   — backend just sent the Share-Location button
 *   - "outside_geofence"    — user is not in any approved zone
 *   - "no_geofence"         — restaurant geofence not configured
 *   - "no_shift"            — reserved / legacy strings that mention shifts
 *   - "invalid_coordinates" — lat/lng could not be parsed
 *   - "no_phone"            — couldn't resolve a phone from context
 *   - "unauthorized"        — agent token rejected (config issue)
 *   - "server_error"        — 5xx from backend
 *   - "network_error"       — couldn't reach backend at all
 */
export default class StaffClockInTool implements LuaTool {
    name = "staff_clock_in";
    description =
        "Clock in a staff member by their phone (WhatsApp is the canonical staff attendance channel — everyone clocks in/out there; this tool backs that when you must write the event from chat). Use when staff say 'clock in', 'clock-in', 'I want to clock in', etc. Pass their phone from WhatsApp context; if they just shared their location, pass latitude and longitude for geofence validation. A scheduled shift is not required — unplanned clock-ins are allowed when location checks pass. Staff with multiple shifts in a day still get the right shift linked when they clock in within the usual window. ALWAYS reply to the staff with the exact 'message' field returned by this tool, character-for-character — that string is already in the staff's language and is the source of truth. NEVER substitute your own apology like 'something went wrong' or 'please try again in a moment' — those are not what the backend said.";

    inputSchema = z
        .object({
            phone: z
                .preprocess((v) => (v === undefined || v === null ? "" : String(v)), z.string())
                .describe(
                    "Sender phone if known; may be empty — the tool resolves WhatsApp/channel uid (e.g. whatsapp:+212…) automatically.",
                ),
            latitude: optionalCoord.describe("Latitude if the user shared their location (number or numeric string)"),
            longitude: optionalCoord.describe("Longitude if the user shared their location (number or numeric string)"),
            location: locationAttachmentSchema.describe(
                "Optional nested location object from WhatsApp/Meta when coordinates are not top-level.",
            ),
        })
        .passthrough();

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    /**
     * Best-effort mapping from backend `error` strings to a stable code
     * Miya can branch on. Falls back to "server_error" / "network_error"
     * when nothing matches; the message is still relayed verbatim.
     */
    private mapErrorToCode(rawError: string | undefined | null): string {
        const e = String(rawError || "").toLowerCase();
        if (!e) return "server_error";
        if (e.includes("location required")) return "location_required";
        if (e.includes("outside geofence")) return "outside_geofence";
        if (e.includes("geofence not configured")) return "no_geofence";
        if (e.includes("no shift") || e.includes("no scheduled shift")) return "no_shift";
        if (e.includes("invalid coordinates")) return "invalid_coordinates";
        if (e.includes("invalid or missing phone")) return "no_phone";
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
                    "We couldn't read your location from this message. Please tap Share Location / Current Location in WhatsApp, or try again.",
            };
        }
        const input = parsed.data as z.infer<typeof this.inputSchema>;

        const user = await User.get();
        if (!user) {
            return {
                status: "error",
                code: "no_context",
                message:
                    "I can't access your account context right now. Please try again in a moment.",
            };
        }
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};

        // Clock-in-by-phone API accepts only the shared Lua webhook key (see ApiService.clockInByPhone).
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

        const phone = resolveStaffPhoneForByPhoneTools(
            { uid: (user as any).uid, data: userData as Record<string, unknown>, _luaProfile: profile },
            input.phone,
        );

        if (!phone || phone.length < 6) {
            console.error(`[StaffClockInTool] No valid phone: input=${input.phone}, uid=${(user as any).uid}`);
            return {
                status: "error",
                code: "no_phone",
                message:
                    "We couldn't find your account. Please contact your manager to be added.",
            };
        }

        try {
            const { lat: latN, lng: lngN } = latLngFromInput(input as Record<string, unknown>);

            console.log(
                `[StaffClockInTool] Calling clock-in-by-phone (phone=${phone.slice(-4).padStart(phone.length, "*")}, lat=${latN ?? "-"}, lng=${lngN ?? "-"})`,
            );
            const result = await this.apiService.clockInByPhone({
                phone,
                latitude: latN,
                longitude: lngN,
            });

            // Always log the structured outcome so we can correlate
            // production tickets with what the backend actually said.
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

            // Normal WhatsApp flow: backend sends Share-Location button — not a failure.
            if (code === "location_required") {
                return {
                    status: "success",
                    code: "location_required",
                    message,
                    miya_directive:
                        "This is the expected next step — NOT an error. Relay the message field VERBATIM. " +
                        "Do NOT say 'there was an error' or 'try again later'. The Share Location button was sent.",
                };
            }

            return {
                status: "error",
                code,
                message,
                miya_directive:
                    "Relay the message field VERBATIM to the staff. Do NOT substitute a generic apology " +
                    "like 'there was an error when trying to clock you in'.",
            };
        } catch (error: any) {
            const em = String(error?.message || error || "");
            if (/Buffer|ArrayBuffer|first argument must be of type string/i.test(em)) {
                console.error("[StaffClockInTool] Transport/encoding error (masked for user):", em.slice(0, 200));
                return {
                    status: "error",
                    code: "network_error",
                    message:
                        "We couldn't finish processing your clock-in. Please try sharing your location again in a moment, or contact your manager.",
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
                    "We couldn't reach the clock-in service right now. Please share your location once more in a minute, or contact your manager.",
            };
        }
    }
}
