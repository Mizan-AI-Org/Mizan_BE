import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class BusinessContextTool implements LuaTool {
    name = "get_business_context";
    description = "Retrieve the restaurant's operational rules, including business hours, peak period definitions (like lunch/dinner times), and default settings. Use this to resolve ambiguous terms autonomously.";

    inputSchema = z.object({
        restaurantId: z.string().optional().describe("Restaurant ID (from context)"),
        query: z.enum(["hours", "peaks", "all"]).default("all").describe("Specific context to retrieve")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const restaurantId =
            input.restaurantId ||
            (context?.get ? context.get("restaurantId") : undefined) ||
            context?.user?.data?.restaurantId;

        const token =
            context?.metadata?.token ||
            (context?.get ? context.get("token") : undefined) ||
            context?.user?.data?.token ||
            context?.user?.token;

        if (!restaurantId || !token) {
            return {
                status: "error",
                message: "Missing restaurantId or authentication token in context."
            };
        }

        try {
            console.log(`[BusinessContextTool] Fetching context for restaurant ${restaurantId}...`);
            const details = await this.apiService.getRestaurantDetails(restaurantId, token);

            if (!details) {
                return { status: "error", message: "Could not retrieve restaurant details." };
            }

            // Default peak definitions if not explicitly in DB
            const peakDefinitions = {
                lunch: { start: "12:00", end: "15:00" },
                dinner: { start: "19:00", end: "23:00" },
                breakfast: { start: "07:00", end: "10:30" }
            };

            const response: any = {
                status: "success",
                restaurant_name: details.name,
                timezone: details.timezone || "America/New_York",
                currency: details.currency || "USD"
            };

            if (input.query === "hours" || input.query === "all") {
                response.operating_hours = details.operating_hours || {};
            }

            if (input.query === "peaks" || input.query === "all") {
                // In a real app, these might come from general_settings or a dedicated model
                response.peak_periods = details.general_settings?.peak_periods || peakDefinitions;
            }

            response.default_break_duration = details.break_duration || 30;

            return response;
        } catch (error: any) {
            console.error("[BusinessContextTool] Execution failed:", error.message);
            return {
                status: "error",
                message: `Failed to retrieve business context: ${error.message}`
            };
        }
    }
}
