/**
 * Enhanced demand forecasting tool.
 * Now reads historical forecasting-events from the Data API
 * and uses AI.generate() to produce intelligent predictions.
 */
import { LuaTool, User, AI, Data, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError, upstreamError } from "./_common/errors";

export default class DemandForecastTool implements LuaTool {
  name = "demand_forecast";
  description =
    "Predict sales and customer footfall based on historical data, past events, and " +
    "operational patterns. Use this to find peak hours, occupancy predictions, and " +
    "staffing recommendations. Now powered by AI analysis of historical event data.";
  inputSchema = z.object({
    date: z.string().describe("Date to forecast (YYYY-MM-DD)"),
    include_recommendations: z
      .boolean()
      .optional()
      .default(true)
      .describe("Include AI-generated staffing/prep recommendations"),
    restaurantId: z
      .string()
      .optional()
      .describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
  });
  private apiService: ApiService;

  constructor(apiService?: ApiService) {
    this.apiService = apiService || new ApiService();
  }

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return noContextError();

    const userData = (user as { data?: Record<string, unknown> }).data || {};
    const profile =
      (user as unknown as { _luaProfile?: Record<string, unknown> })._luaProfile || {};
    const restaurantId =
      input.restaurantId ||
      (user as { restaurantId?: string }).restaurantId ||
      userData.restaurantId ||
      profile.restaurantId;

    const agentKey =
      env("LUA_WEBHOOK_API_KEY") ||
      env("WEBHOOK_API_KEY") ||
      env("MIZAN_SERVICE_TOKEN");

    if (!restaurantId) {
      return noContextError({ hint: "Restaurant ID is required for forecasting." });
    }

    try {
      const restaurant = await this.apiService.getRestaurantDetails(
        restaurantId as string,
        agentKey || ""
      );

      const peakPeriods =
        (restaurant as { general_settings?: { peak_periods?: string[] } })
          ?.general_settings?.peak_periods || ["12:00-15:00", "19:00-23:00"];

      const restaurantName = restaurant?.name || "Restaurant";

      // Query historical forecasting events for context
      let historicalContext = "";
      try {
        const events = await Data.search(
          "forecasting-events",
          `forecast demand sales ${restaurantId} ${input.date}`,
          10,
          0.5
        );
        if (events.length > 0) {
          historicalContext = events
            .map(
              (e) =>
                `[${e.createdAt || ""}] ${e.type || e.event_type || ""}: ${e.summary || e.description || JSON.stringify(e.data || {}).slice(0, 200)}`
            )
            .join("\n");
        }
      } catch {
        // Data collection may not exist yet
      }

      // Query staff management events for attendance patterns
      let staffContext = "";
      try {
        const staffEvents = await Data.search(
          "staff-management-events",
          `attendance clock-in no-show ${restaurantId}`,
          5,
          0.5
        );
        if (staffEvents.length > 0) {
          staffContext = staffEvents
            .map(
              (e) =>
                `${e.type || e.event_type || ""}: ${e.summary || e.description || ""}`
            )
            .join("\n");
        }
      } catch {
        // Collection may not exist
      }

      if (!input.include_recommendations || (!historicalContext && !staffContext)) {
        return {
          status: "success",
          date: input.date,
          restaurant: restaurantName,
          forecast: {
            peak_hours: peakPeriods,
            note: "Based on configured peak periods. More accurate forecasting available once event history builds up.",
          },
          miya_directive:
            "Present the peak hours to the user in their language. Mention that predictions will become more accurate as more operational data is collected.",
        };
      }

      // Use AI to generate intelligent forecast
      const forecastText = await AI.generate(
        "You are a restaurant operations analyst. Generate a concise demand forecast " +
          "based on the data provided. Include: expected busy periods, staffing recommendation " +
          "(understaffed/overstaffed/optimal), and 1-2 actionable prep tips. Be specific with " +
          "numbers when possible. Keep it under 150 words. Return plain text, not JSON.",
        [
          {
            type: "text",
            text: [
              `Restaurant: ${restaurantName}`,
              `Forecast date: ${input.date}`,
              `Day of week: ${new Date(input.date).toLocaleDateString("en-US", { weekday: "long" })}`,
              `Configured peak periods: ${peakPeriods.join(", ")}`,
              historicalContext
                ? `\nHistorical events:\n${historicalContext}`
                : "",
              staffContext
                ? `\nStaff attendance patterns:\n${staffContext}`
                : "",
            ]
              .filter(Boolean)
              .join("\n"),
          },
        ]
      );

      return {
        status: "success",
        date: input.date,
        restaurant: restaurantName,
        forecast: {
          peak_hours: peakPeriods,
          ai_analysis: forecastText,
          data_sources: [
            ...(historicalContext ? ["forecasting-events"] : []),
            ...(staffContext ? ["staff-attendance-patterns"] : []),
            "restaurant-settings",
          ],
        },
        miya_directive:
          "Present the AI analysis to the user in their language. Lead with the key insight, then the staffing recommendation. If they ask for more detail, offer to check sales_summary or attendance_report.",
      };
    } catch (error) {
      return upstreamError((error as Error).message);
    }
  }
}
