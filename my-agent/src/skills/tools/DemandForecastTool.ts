import { LuaTool } from "lua-cli";
import { z } from "zod";

export default class DemandForecastTool implements LuaTool {
    name = "demand_forecast";
    description = "Predict sales and customer footfall based on historical data, events, and weather.";

    inputSchema = z.object({
        date: z.string().describe("Date to forecast (YYYY-MM-DD)"),
        restaurantId: z.string().optional().describe("The ID of the restaurant (from context)"),
        restaurantName: z.string().optional().describe("The name of the restaurant (from context)")
    });

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const restaurantId = input.restaurantId || (context?.get ? context.get("restaurantId") : undefined);
        const restaurantName = input.restaurantName || (context?.get ? context.get("restaurantName") : "Unknown Restaurant");

        if (!restaurantId) {
            return { status: "error", message: "No restaurant context found. Please ensure you are logged in." };
        }

        console.log(`[DemandForecastTool] Executing for ${restaurantName} (${restaurantId})`);

        // Simulated logic with Moroccan context
        return {
            date: input.date,
            restaurant: restaurantName,
            forecast: {
                expected_revenue: "15,000 MAD",
                expected_covers: 120,
                peak_hours: ["13:00-14:30", "20:00-22:00"],
            },
            factors: [
                "Local Holiday: Eid Al-Fitr (High demand expected)",
                "Weather: Sunny, 28°C (Terrace seating optimized)",
                "Tourist Season: High (Marrakech influx)"
            ],
            recommendations: [
                "Prepare extra Tagine ingredients.",
                "Ensure full staff for dinner service."
            ]
        };
    }
}
