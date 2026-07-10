import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { resolveAgentContext } from "../../services/agentContext";

/**
 * Workspace operational context — hours, peaks, and multi-vertical playbook.
 * restaurant_id is the tenant/workspace id for every business sector.
 */
export default class BusinessContextTool implements LuaTool {
    name = "get_business_context";
    description =
        "Retrieve this workspace's operational rules: business_vertical (sector), peak periods, " +
        "hours, currency/timezone, and a sector playbook (vocabulary, priorities, hard rules). " +
        "Call this when resolving time words (lunch/dinner/morning/shift) OR when you need to " +
        "sound expert for retail, construction, healthcare ops, hospitality, manufacturing, services, etc. " +
        "ALWAYS pass restaurantId from [SYSTEM: PERSISTENT CONTEXT].";

    inputSchema = z.object({
        restaurantId: z
            .string()
            .optional()
            .describe("ALWAYS pass the Restaurant ID (workspace tenant id) from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
        query: z
            .enum(["hours", "peaks", "vertical", "all"])
            .default("all")
            .describe("Specific context to retrieve"),
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const ctx = await resolveAgentContext(input.restaurantId);
        const restaurantId = ctx.restaurantId;
        const token = ctx.token;

        if (!restaurantId) {
            return {
                status: "error",
                message: "Missing restaurantId (workspace id) in context.",
            };
        }

        try {
            console.log(`[BusinessContextTool] Fetching context for workspace ${restaurantId}...`);
            let details: any;

            if (token) {
                details = await this.apiService.getRestaurantDetails(restaurantId, token);
            } else {
                details = await this.apiService.getRestaurantDetailsForAgent(restaurantId);
            }

            if (!details) {
                console.warn("[BusinessContextTool] Could not retrieve details, using defaults.");
                details = {
                    name: "Workspace",
                    operating_hours: {},
                    business_vertical: "RESTAURANT",
                    timezone: "Africa/Casablanca",
                    currency: "MAD",
                };
            }

            const bv = String(
                details.business_vertical ||
                    details.general_settings?.business_vertical ||
                    "RESTAURANT",
            ).toUpperCase();

            const playbook = details.vertical_playbook || null;
            const defaultPeaks =
                playbook?.peak_periods ||
                details.general_settings?.peak_periods || {
                    lunch: { start: "12:00", end: "15:00" },
                    dinner: { start: "19:00", end: "23:00" },
                    breakfast: { start: "07:00", end: "10:30" },
                    morning: { start: "09:00", end: "12:00" },
                    afternoon: { start: "13:00", end: "18:00" },
                };

            const response: Record<string, unknown> = {
                status: "success",
                workspace_name: details.name,
                restaurant_name: details.name,
                restaurant_id: restaurantId,
                business_vertical: bv,
                timezone: details.timezone || "Africa/Casablanca",
                currency: details.currency || "MAD",
                country_code: details.country_code || "MA",
                miya_directive:
                    "Adapt ALL language, examples, and time-word resolution to business_vertical. " +
                    "restaurant_id is the tenant id for every sector — not proof the business is a restaurant. " +
                    "HEALTHCARE: operations only — never medical advice. " +
                    "Be brilliantly proactive for THIS sector.",
            };

            if (input.query === "hours" || input.query === "all") {
                response.operating_hours = details.operating_hours || {};
            }

            if (input.query === "peaks" || input.query === "all" || input.query === "vertical") {
                response.peak_periods = defaultPeaks;
            }

            if (input.query === "vertical" || input.query === "all") {
                response.vertical_playbook = playbook || {
                    business_vertical: bv,
                    note: "Full playbook unavailable — still respect business_vertical wording.",
                };
            }

            response.restaurant_type = details.restaurant_type || null;
            response.scheduling_policy = {
                max_weekly_hours: details.max_weekly_hours || 40,
                min_rest_hours: details.min_rest_hours || 11,
            };
            response.default_break_duration = details.break_duration || 30;
            if (details.general_settings?.ramadan_mode) {
                response.ramadan = {
                    enabled: true,
                    iftar_time: details.general_settings.iftar_time,
                    suhoor_time: details.general_settings.suhoor_time,
                };
            }

            return response;
        } catch (error: any) {
            console.error("[BusinessContextTool] Execution failed:", error.message);
            return {
                status: "error",
                message: `Failed to retrieve business context: ${error.message}`,
            };
        }
    }
}
