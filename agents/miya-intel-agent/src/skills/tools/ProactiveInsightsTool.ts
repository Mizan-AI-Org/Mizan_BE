import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

/**
 * Proactive intelligence: no-shows, understaffed shifts, late patterns, staffing suggestions.
 * Miya can call this at conversation start or when the manager asks "what do I need to know?" to surface alerts.
 */
export default class ProactiveInsightsTool implements LuaTool {
    name = "get_proactive_insights";
    description =
        "Get proactive operational insights: no-shows or missing clock-ins today, understaffing risk, late clock-ins, and staffing suggestions. " +
        "Call this when the user opens the chat or asks 'what should I know?', 'any alerts?', 'how are we looking today?', or before discussing the schedule.";

    inputSchema = z.object({
        date: z.string().optional().describe("Date (YYYY-MM-DD). Default: today."),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) {
            return { status: "error", message: "I can't access your context right now." };
        }
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};
        const metadata = (profile.metadata && typeof profile.metadata === "object" ? profile.metadata : {}) as Record<string, string>;
        const restaurantId =
            input.restaurantId ||
            (user as any).restaurantId ||
            userData.restaurantId ||
            profile.restaurantId ||
            metadata?.restaurantId ||
            metadata?.restaurant_id;
        const token =
            (user as any).token ||
            userData.token ||
            profile.token ||
            metadata?.token ||
            metadata?.accessToken;

        if (!restaurantId) {
            return { status: "error", message: "Restaurant context is missing." };
        }

        try {
            const result = await this.apiService.getProactiveInsightsForAgent(
                restaurantId,
                input.date || new Date().toISOString().split("T")[0],
                token
            );
            if (!result.insights || result.insights.length === 0) {
                return {
                    status: "success",
                    message: "No alerts or insights for this date. Operations look on track.",
                    insights: [],
                    has_alerts: false,
                    date: result.date,
                };
            }
            const summary = result.insights
                .map((i) => `[${i.priority}] ${i.title}: ${i.summary}`)
                .join("\n");
            return {
                status: "success",
                message: result.has_alerts
                    ? `There are ${result.insights.length} insight(s) for today. Summarize these for the manager and offer to act.`
                    : `Insights for ${result.date}: ${summary}`,
                insights: result.insights,
                has_alerts: result.has_alerts,
                date: result.date,
            };
        } catch (error: any) {
            return { status: "error", message: error?.message || "Failed to load insights." };
        }
    }
}
