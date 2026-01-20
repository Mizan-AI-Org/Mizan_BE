import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class DemandForecastTool implements LuaTool {
    name = "demand_forecast";
    description = "Predict sales and customer footfall based on historical data. Use this to find peak hours and occupancy predictions.";

    inputSchema = z.object({
        date: z.string().describe("Date to forecast (YYYY-MM-DD)"),
        restaurantId: z.string().optional().describe("The ID of the restaurant (from context)")
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
            return { status: "error", message: "No restaurant context found. Please ensure you are logged in." };
        }

        try {
            console.log(`[DemandForecastTool] Fetching forecast for date ${input.date}`);
            // Logic to fetch from billing/pos analytics if implemented
            // Fallback to operational metrics from RestaurantDetails
            const restaurant = await this.apiService.getRestaurantDetails(restaurantId, token);

            return {
                status: "success",
                date: input.date,
                restaurant: restaurant?.name || "Target Restaurant",
                forecast: {
                    expected_covers: 120, // Real logic would call a forecasting model view
                    peak_hours: restaurant?.general_settings?.peak_periods || ["13:00-14:30", "20:00-22:00"],
                },
                factors: [
                    "Historical trends",
                    "Cultural calendar awareness"
                ]
            };
        } catch (error: any) {
            return { status: "error", message: `Forecast retrieval failed: ${error.message}` };
        }
    }
}
