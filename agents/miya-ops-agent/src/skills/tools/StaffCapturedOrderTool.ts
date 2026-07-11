import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { resolveAgentContext } from "../../services/agentContext";

function isVoiceUiPlaceholder(text: string): boolean {
    const s = text.trim();
    if (s.length > 120) return false;
    if (/voice\s*message|message\s+vocal|note\s+vocale|رسالة\s*صوتية|audio\s*message|message\s+audio/i.test(s)) {
        return true;
    }
    if (s.includes("🎤") && /\(\s*\d+\s*:\s*\d+\s*\)/.test(s)) return true;
    return false;
}

function extractTranscriptFromContext(context?: any): string {
    if (!context || typeof context !== "object") return "";
    const c = context as Record<string, unknown>;
    const tryKeys = [
        c.transcript,
        c.voiceTranscript,
        (c.metadata as any)?.transcript,
        (c.metadata as any)?.voiceTranscript,
        (c.metadata as any)?.speechToText,
        (c.metadata as any)?.lastUserMessage,
        (c.message as any)?.transcript,
        (c.message as any)?.body,
    ];
    for (const v of tryKeys) {
        if (typeof v === "string" && v.trim().length >= 4) return v.trim();
    }
    return "";
}

/**
 * Logs staff-captured guest orders to Mizan "Today's Orders" (StaffCapturedOrder).
 * Calls detectOrderStationForAgent first when possible and passes station / clarification.
 */
export default class StaffCapturedOrderTool implements LuaTool {
    name = "capture_guest_order";
    description =
        "Log a guest / customer order taken by staff (Today's Orders in Mizan). Use when staff dictate or type " +
        "items, table number, pickup, takeout, delivery, or phone-in details — via WhatsApp text or voice transcript. " +
        "This creates a staff-captured order record; it is NOT the same as syncing the electronic POS. " +
        "NEVER tell the user to use the POS instead — call this tool. " +
        "ALWAYS pass phone from context. Use report_incident only for safety issues, injuries, equipment failure, or complaints — not for routine order-taking.";

    inputSchema = z.object({
        items_summary: z.string().describe(
            "Full order text: items, quantities, guest name if given, table, pickup/takeout, special instructions. " +
                "For voice, use the transcript. Minimum useful detail (not empty)."
        ),
        restaurantId: z.string().optional().describe("Restaurant UUID from [SYSTEM: PERSISTENT CONTEXT]."),
        phone: z.string().optional().describe("Staff phone from context (User Phone). REQUIRED for attribution."),
        source: z.enum(["text", "voice"]).optional().describe("How the order was sent."),
        order_type: z.enum(["DINE_IN", "TAKEOUT", "DELIVERY", "OTHER"]).optional().describe("Default DINE_IN if unknown."),
        customer_name: z.string().optional(),
        customer_phone: z.string().optional(),
        table_or_location: z.string().optional(),
        role: z.string().optional().describe("Staff role hint for station detection (Bar/Floor/Kitchen)."),
        station: z.string().optional().describe("Bar / Floor / Kitchen if already known or clarified by staff."),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    private resolvePhone(user: any, inputPhone?: string, ctxPhone?: string, context?: any): string | undefined {
        if (inputPhone) {
            const digits = String(inputPhone).replace(/\D/g, "");
            if (digits.length >= 6) return digits;
        }
        if (ctxPhone) return ctxPhone;

        const userData = user ? ((user as any).data || {}) : {};
        const profile = user ? ((user as any)._luaProfile || {}) : {};
        const metadata = profile.metadata && typeof profile.metadata === "object" ? profile.metadata : {};

        const candidates = [
            context?.user?.phone,
            context?.metadata?.phone,
            context?.user?.data?.phone,
            context?.channel?.phone,
            context?.metadata?.details?.phone,
            context?.event?.from,
            context?.message?.from,
            (context?.metadata?.details as any)?.phone,
            userData.phone,
            (metadata as any)?.phone,
            profile.phoneNumber,
            profile.mobileNumber,
        ];

        let phone = candidates.find((p) => p && String(p).replace(/\D/g, "").length >= 6);
        if (phone) return String(phone).replace(/\D/g, "");

        const uid = (user as any)?.uid ? String((user as any).uid) : "";
        if (uid) {
            const afterColon = uid.includes(":") ? uid.split(":").slice(1).join(":").trim() : uid;
            const digits = (afterColon || uid).replace(/\D/g, "");
            if (digits.length >= 6) return digits;
        }

        return undefined;
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const ctx = await resolveAgentContext(input.restaurantId);
        const user = await User.get();

        let staffPhone = this.resolvePhone(user, input.phone, ctx.phone, context);
        let restaurantId = ctx.restaurantId;
        let staffRole =
            input.role ||
            (user as any)?.data?.role ||
            (user as any)?.data?.position ||
            (user as any)?._luaProfile?.role ||
            "";

        let rawText =
            (input.items_summary ?? "").trim() ||
            (context?.message?.body && String(context.message.body).trim()) ||
            (context?.lastMessage?.text && String(context.lastMessage.text).trim()) ||
            "";

        const transcriptHint = extractTranscriptFromContext(context);
        if (isVoiceUiPlaceholder(rawText) && transcriptHint.length >= 4) {
            rawText = transcriptHint;
        }

        if (rawText.length < 3) {
            return {
                status: "error",
                message:
                    "I need the order details (items, table, or pickup info). Please send a voice note or type the order.",
            };
        }

        if (isVoiceUiPlaceholder(rawText) && transcriptHint.length < 4) {
            return {
                status: "error",
                message:
                    "I got your voice note, but I don’t have the spoken details on this channel yet. Please type the order (items, quantities, table or pickup) and I’ll log it for the team right away.",
            };
        }

        if ((!restaurantId || !staffPhone || !staffRole) && staffPhone) {
            try {
                const staffLookup = await this.apiService.getStaffByPhoneForAgent(staffPhone);
                if (staffLookup.success && staffLookup.found && staffLookup.staff) {
                    if (!restaurantId) restaurantId = staffLookup.staff.restaurant_id || undefined;
                    if (!staffRole) {
                        staffRole =
                            staffLookup.staff.role ||
                            staffLookup.staff.position ||
                            staffLookup.staff.department ||
                            "";
                    }
                }
            } catch {
                /* ignore */
            }
        }

        if (!restaurantId) {
            return {
                status: "error",
                message:
                    "I couldn't link this order to your restaurant. Please message from the phone number on your staff profile.",
            };
        }

        if (!staffPhone) {
            return {
                status: "error",
                message: "I need your staff phone in context to log the order. Contact your manager if this persists.",
            };
        }

        let station = (input.station || "").trim() || undefined;
        let needsClarification = false;
        let stationMessage: string | undefined;
        try {
            const detected = await this.apiService.detectOrderStationForAgent(
                restaurantId,
                { role: staffRole || undefined },
                ctx.token,
            );
            if (!station && detected.station) {
                station = detected.station;
            }
            needsClarification = !!detected.needs_clarification && !station;
            stationMessage = detected.message;
        } catch {
            /* proceed without station */
        }

        if (needsClarification && !station) {
            return {
                status: "needs_clarification",
                needs_clarification: true,
                message:
                    stationMessage ||
                    "Which station is this for — Bar, Floor, or Kitchen? Reply with the station and I’ll log the order.",
            };
        }

        const channel = input.source === "voice" ? "VOICE" : "TEXT";

        try {
            const result = await this.apiService.createStaffCapturedOrderForAgent({
                restaurant_id: restaurantId,
                items_summary: rawText.slice(0, 8000),
                staff_phone: staffPhone,
                channel,
                order_type: input.order_type || "DINE_IN",
                customer_name: input.customer_name,
                customer_phone: input.customer_phone,
                table_or_location: input.table_or_location,
                station,
                detected_station: station,
            });

            if (result.success === false || (result as any).error) {
                const err = (result as any).error || "Could not save the order.";
                return { status: "error", message: typeof err === "string" ? err : "Could not save the order." };
            }

            const shortId = ((result as any).short_id || (result as any).order_id || "").toString().slice(0, 8);
            const preview = rawText.length > 350 ? `${rawText.slice(0, 350)}…` : rawText;
            const src = input.source === "voice" || channel === "VOICE" ? "voice" : "message";
            const stationLabel = station ? ` (${station})` : "";

            const userMessage =
                `Thanks — I’ve saved this guest order for the team${stationLabel}.\n\n` +
                `Here’s what I logged:\n${preview}\n\n` +
                (src === "voice"
                    ? `The kitchen and floor can see it under Today’s Orders. If anything needs changing, just send an update here.`
                    : `If anything needs changing, reply with the correction and I’ll pass it along.`);

            return {
                status: "success",
                message: userMessage,
                userMessage,
                station: station || (result as any).detected_station || null,
                details: { orderId: (result as any).order_id, shortId, station },
            };
        } catch (error: any) {
            console.error("[StaffCapturedOrderTool]", error.message);
            return {
                status: "error",
                message: error.response?.data?.error || error.message || "Could not save the order.",
            };
        }
    }
}
