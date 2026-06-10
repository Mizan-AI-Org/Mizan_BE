/**
 * InventoryCountTool — Conversational inventory counting via WhatsApp.
 * Miya walks staff through items one by one. Staff reports quantity for each.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class InventoryCountTool implements LuaTool {
    name = "inventory_count";
    description =
        "Start or continue an inventory count session. Use 'start' when a manager or staff says " +
        "'start inventory count', 'comptage stock', 'count inventory', 'عد المخزون'. " +
        "Use 'count' to record the quantity for the current item in an active session.";

    inputSchema = z.object({
        action: z.enum(["start", "count"]).describe("'start' to begin a new count session, 'count' to record quantity for current item"),
        session_id: z.string().optional().describe("Required for action='count'. The active session ID."),
        counted_quantity: z.number().optional().describe("Required for action='count'. The counted quantity."),
        category: z.string().optional().describe("Optional filter when starting: only count items matching this category/keyword"),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    private apiService: ApiService;
    constructor(apiService?: ApiService) { this.apiService = apiService || new ApiService(); }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const user = await User.get();
        const restaurantId = input.restaurantId || (user as any)?.data?.restaurantId || context?.metadata?.restaurantId;

        if (input.action === "start") {
            if (!restaurantId) return { status: "error", message: "I couldn't determine which restaurant to count for." };
            const staffId = (user as any)?.data?.staffId || (user as any)?.data?.id;
            const result = await this.apiService.startInventoryCount(restaurantId, staffId, input.category);
            return {
                status: result.success ? "counting" : "error",
                session_id: result.session_id,
                total_items: result.total_items,
                current_item: result.current_item,
                message: result.message_for_user || result.error,
            };
        }

        if (input.action === "count") {
            if (!input.session_id) return { status: "error", message: "No active count session. Say 'start inventory count' to begin." };
            if (input.counted_quantity === undefined) return { status: "error", message: "Please tell me the quantity." };

            const result = await this.apiService.countInventoryItem(input.session_id, input.counted_quantity);
            return {
                status: result.success ? (result.done ? "completed" : "counting") : "error",
                session_id: input.session_id,
                done: result.done,
                items_counted: result.items_counted,
                items_remaining: result.items_remaining,
                current_item: result.current_item,
                message: result.message_for_user || result.error,
            };
        }

        return { status: "error", message: "Invalid action." };
    }
}
