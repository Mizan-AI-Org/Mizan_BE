/**
 * CashReconciliationTool — Cash drawer management per shift.
 * Open drawer at shift start, count cash at shift end.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class CashReconciliationTool implements LuaTool {
    name = "cash_reconciliation";
    description =
        "Manage cash drawer sessions. Actions: " +
        "'open' — open the cash drawer at shift start (specify opening float). " +
        "'close' — staff counts the cash at shift end; system computes variance. " +
        "Use when staff say 'open drawer', 'cash count', 'close cash', 'count the cash', " +
        "'comptage caisse', 'فتح الصندوق', 'حساب الكاش'.";

    inputSchema = z.object({
        action: z.enum(["open", "close"]).describe("'open' to start a cash session, 'close' to count and close"),
        opening_float: z.number().optional().describe("For 'open': starting cash amount in MAD"),
        counted_cash: z.number().optional().describe("For 'close': total cash counted in drawer"),
        variance_reason: z.string().optional().describe("For 'close': staff explanation if there's a variance"),
        session_id: z.string().optional().describe("For 'close': specific session to close (auto-detected if omitted)"),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    private apiService: ApiService;
    constructor(apiService?: ApiService) { this.apiService = apiService || new ApiService(); }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const user = await User.get();
        const restaurantId = input.restaurantId || (user as any)?.data?.restaurantId || context?.metadata?.restaurantId;
        const staffId = (user as any)?.data?.staffId || (user as any)?.data?.id;

        if (input.action === "open") {
            if (!restaurantId || !staffId) return { status: "error", message: "I need to know the restaurant and who's opening the drawer." };
            const result = await this.apiService.openCashSession(restaurantId, staffId, input.opening_float || 0);
            return {
                status: result.success ? "opened" : "error",
                session_id: result.session_id,
                message: result.message_for_user || result.error,
            };
        }

        if (input.action === "close") {
            if (input.counted_cash === undefined) return { status: "error", message: "Please tell me the total cash in the drawer." };
            const result = await this.apiService.closeCashSession({
                session_id: input.session_id,
                restaurant_id: restaurantId,
                staff_id: staffId,
                counted_cash: input.counted_cash,
                variance_reason: input.variance_reason,
            });
            return {
                status: result.success ? "closed" : "error",
                variance: result.variance,
                session_status: result.status,
                message: result.message_for_user || result.error,
            };
        }

        return { status: "error", message: "Invalid action. Use 'open' or 'close'." };
    }
}
