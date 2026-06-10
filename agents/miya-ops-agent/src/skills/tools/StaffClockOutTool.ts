import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { resolveStaffPhoneForByPhoneTools } from "../../utils/resolveStaffPhoneFromLuaUser";

export default class StaffClockOutTool implements LuaTool {
    name = "staff_clock_out";
    description =
        "Clock out a staff member by their phone (WhatsApp is the canonical attendance channel — staff clock out there; this tool backs Miya-mediated chat). Use when staff say 'clock out', 'clock-out', 'I want to clock out', etc. Pass their phone from WhatsApp context. Reply with the exact message_for_user returned.";

    inputSchema = z.object({
        phone: z
            .preprocess((v) => (v === undefined || v === null ? "" : String(v)), z.string())
            .describe("Sender phone if known; may be empty — resolved from WhatsApp/channel uid automatically."),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) {
            return { status: "error", message: "I can't access your account context right now. Please try again in a moment." };
        }
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};
        const hasAgentKey = !!(env("LUA_WEBHOOK_API_KEY") || env("WEBHOOK_API_KEY") || env("MIZAN_SERVICE_TOKEN"));
        if (!hasAgentKey) {
            return { status: "error", message: "I encountered an issue while trying to clock you out. Please try again later or contact support." };
        }
        const phone = resolveStaffPhoneForByPhoneTools(
            { uid: (user as any).uid, data: userData as Record<string, unknown>, _luaProfile: profile },
            input.phone,
        );
        if (!phone || phone.length < 6) {
            return { status: "error", message: "I couldn't determine your phone number. Please try again or contact your manager." };
        }
        try {
            const result = await this.apiService.clockOutByPhone(phone);
            if (result.success) {
                return { status: "success", message: result.message_for_user || "You're clocked out. Have a great rest of your day!" };
            }
            return { status: "error", message: result.message_for_user || result.error || "I couldn't clock you out. Please try again." };
        } catch (error: any) {
            const em = String(error?.message || error || "");
            if (/Buffer|ArrayBuffer|first argument must be of type string/i.test(em)) {
                return {
                    status: "error",
                    message: "We couldn't finish processing your clock-out. Please try again in a moment or contact your manager.",
                };
            }
            return { status: "error", message: (error.response?.data?.message_for_user as string) || "I encountered an issue. Please try again or contact your manager." };
        }
    }
}
