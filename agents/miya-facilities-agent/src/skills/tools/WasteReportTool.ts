/**
 * WasteReportTool — Staff report food waste via WhatsApp.
 * "Threw away 3 tagine portions", "5kg chicken expired", "waste 2 bread loaves".
 */
import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class WasteReportTool implements LuaTool {
    name = "report_waste";
    description =
        "Log food waste reported by staff. Use when staff say things like 'threw away', 'wasted', 'expired', " +
        "'gaspillage', 'jeté', 'راه تهدر', 'سالي' + a food item. Also use for waste summary requests.";

    inputSchema = z.object({
        item_name: z.string().describe("Name of the wasted item (e.g. 'chicken', 'bread', 'tagine')"),
        quantity: z.number().describe("How much was wasted"),
        unit: z.string().optional().describe("Unit (kg, portions, pieces, liters). Infer from context."),
        reason: z.enum(["EXPIRED", "SPOILED", "OVERPRODUCTION", "DROPPED", "RETURNED", "QUALITY", "OTHER"]).optional(),
        notes: z.string().optional(),
        summary_only: z.boolean().optional().describe("If true, return today's waste summary instead of logging"),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    private apiService: ApiService;
    constructor(apiService?: ApiService) { this.apiService = apiService || new ApiService(); }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const user = await User.get();
        const restaurantId = input.restaurantId || (user as any)?.data?.restaurantId || context?.metadata?.restaurantId;
        if (!restaurantId) return { status: "error", message: "I couldn't determine which restaurant you're in." };

        if (input.summary_only) {
            const result = await this.apiService.getWasteSummary(restaurantId);
            return { status: result.success ? "ok" : "error", message: result.message_for_user || result.error };
        }

        const staffId = (user as any)?.data?.staffId || (user as any)?.data?.id || context?.metadata?.staffId;
        const result = await this.apiService.reportWaste(restaurantId, {
            item_name: input.item_name,
            quantity: input.quantity,
            unit: input.unit,
            reason: input.reason,
            staff_id: staffId,
            notes: input.notes,
        });

        return { status: result.success ? "recorded" : "error", message: result.message_for_user || result.error };
    }
}
