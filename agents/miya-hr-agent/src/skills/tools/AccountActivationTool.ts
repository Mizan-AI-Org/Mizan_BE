import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { resolveStaffPhoneForByPhoneTools } from "../../utils/resolveStaffPhoneFromLuaUser";

/** Clone for tool return payloads — strips undefined and avoids non-JSON types the host may mishandle. */
function toolResultJsonClone(value: unknown): unknown {
    if (value === undefined || value === null) return undefined;
    if (typeof value !== "object") return value;
    try {
        return JSON.parse(JSON.stringify(value));
    } catch {
        return undefined;
    }
}

export default class AccountActivationTool implements LuaTool {
    name = "account_activation";
    description = "Activate a staff account when they send their first message. Call this with the user's phone (from context). Backend activates by phone and returns a success message—reply to the user with that exact message. Use when staff says 'Hi Mizan AI, I am ready to activate my account!' or similar.";

    inputSchema = z.object({
        phone: z
            .preprocess((v) => (v === undefined || v === null ? "" : String(v)), z.string())
            .describe("The user's phone number as extracted from the context (optional if WhatsApp uid supplies it)"),
        first_name: z.string().optional().describe("User's first name if mentioned"),
        last_name: z.string().optional().describe("User's last name if mentioned"),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) {
            return {
                status: "error",
                message: "I can't access your account context right now. Please try again in a moment."
            };
        }
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};

        const agentKeyTrimmed = String(
            env('LUA_WEBHOOK_API_KEY') ||
                env('WEBHOOK_API_KEY') ||
                env('MIZAN_SERVICE_TOKEN') ||
                ''
        ).trim();
        const hasAgentKey = !!agentKeyTrimmed;

        if (!hasAgentKey) {
            console.error('[AccountActivationTool] No LUA_WEBHOOK_API_KEY / WEBHOOK_API_KEY / MIZAN_SERVICE_TOKEN in environment.');
            return {
                status: "error",
                message: "I can't process your activation right now. Please try again later."
            };
        }

        const phone = resolveStaffPhoneForByPhoneTools(
            { uid: (user as any).uid, data: userData as Record<string, unknown>, _luaProfile: profile },
            input.phone,
        );

        if (!phone || phone.replace(/[^0-9]/g, '').length < 6) {
            console.error(`[AccountActivationTool] No valid phone: input=${input.phone}, uid=${(user as any).uid}`);
            return {
                status: "error",
                message: "I couldn't determine your phone number from this chat. Please try again or contact your manager."
            };
        }

        console.log(`[AccountActivationTool] Activating by phone: ${phone}`);

        try {
            const result = await this.apiService.activateAccountByPhone(phone);

            if (!result.success) {
                // Never show PIN or technical errors to the user during activation
                const raw = (result.error || result.message_for_user || "").toLowerCase();
                const genericMessage = "Your account could not be activated. Please confirm you've received the activation link from your manager and that your phone number matches what they have on file, or contact support.";
                const message = raw && (raw.includes("pin") || raw.includes("password")) ? genericMessage : (result.message_for_user || result.error || genericMessage);
                return { status: "error", message };
            }

            console.log(`[AccountActivationTool] ✅ Account activated for ${result.user?.email}`);

            const restaurantId = result.user?.restaurant?.id;
            const restaurantName = result.user?.restaurant?.name;
            if ((restaurantId || restaurantName) && user && typeof (user as any).save === 'function') {
                try {
                    const prev = ((user as any).data || {}) as Record<string, unknown>;
                    const next: Record<string, unknown> = { ...prev };
                    if (restaurantId || prev.restaurantId) {
                        next.restaurantId = restaurantId || prev.restaurantId;
                    }
                    if (restaurantName || prev.restaurantName) {
                        next.restaurantName = restaurantName || prev.restaurantName;
                    }
                    if (result.user?.first_name || prev.userName) {
                        next.userName = result.user?.first_name || prev.userName;
                    }
                    if (result.user?.role || prev.role) {
                        next.role = result.user?.role || prev.role;
                    }
                    next.phone = phone;
                    (user as any).data = next;
                    await (user as any).save();
                    console.log(`[AccountActivationTool] 📍 Restaurant context persisted: ${restaurantName} (${restaurantId})`);
                } catch (persistErr: any) {
                    console.warn(`[AccountActivationTool] ⚠️ Could not persist restaurant context:`, persistErr?.message);
                }
            }

            const userForAgent = toolResultJsonClone(result.user) as Record<string, unknown> | undefined;
            return {
                status: "success",
                message:
                    result.message_for_user ||
                    "Congratulations! Your account has been successfully activated. Welcome to the team!",
                ...(userForAgent && Object.keys(userForAgent).length > 0 ? { user: userForAgent } : {}),
            };
        } catch (error: any) {
            const em = String(error?.message || error || "");
            if (/Buffer|ArrayBuffer|first argument must be of type string/i.test(em)) {
                console.error("[AccountActivationTool] Transport/encoding error:", em.slice(0, 200));
                return {
                    status: "error",
                    message:
                        "We hit a technical glitch while contacting the activation service. Please try again in a minute, or open Miya from your Mizan dashboard.",
                };
            }
            console.error(`[AccountActivationTool] ❌ Unexpected error:`, error.message);
            return {
                status: "error",
                message: "An unexpected error occurred. Please try again later."
            };
        }
    }
}
