import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class OptimalStaffingTool implements LuaTool {
    name = "get_optimal_staffing";
    description = "Get data-driven staffing recommendations and shift structure suggestsions (like shift splits) for a specific date. Use this to help managers optimize their labor levels.";

    inputSchema = z.object({
        date: z.string().describe("Date to analyze (YYYY-MM-DD)"),
        restaurantId: z.string().describe("The restaurant ID from context")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        try {
            const advice = await this.apiService.getOperationalAdvice(input.restaurantId, input.date);

            if (!advice || advice.error) {
                return {
                    status: "error",
                    message: advice?.error || "Failed to retrieve operational advice."
                };
            }

            return {
                status: "success",
                date: input.date,
                demand_level: advice.demand_level,
                recommendations: advice.optimal_staffing,
                shift_splits: advice.shift_split_suggestions,
                restaurant_style: advice.restaurant_type,
                best_practices: advice.best_practices
            };
        } catch (error: any) {
            return {
                status: "error",
                message: `Operational advice failure: ${error.message}`
            };
        }
    }
}
