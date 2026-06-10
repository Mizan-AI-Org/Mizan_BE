import { LuaTool, User, Lua, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class ScheduleOptimizerTool implements LuaTool {
    name = "schedule_optimizer";
    description = "Optimize staff schedules based on predicted demand and staff availability.";

    inputSchema = z.object({
        week_start: z.string().describe("Start date of the week (YYYY-MM-DD)"),
        department: z.enum(["kitchen", "service", "all"]).optional().describe("Department to optimize"),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit.")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>) {
        console.log('[ScheduleOptimizerTool] V7 Execution started');

        const user = await User.get();
        if (!user) {
            return {
                status: "error",
                message: "I can't access your account context right now. Please try again in a moment."
            };
        }
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};
        const metadata = profile.metadata && typeof profile.metadata === 'object' ? profile.metadata : {};

        const restaurantId =
            input.restaurantId ||
            (user as any).restaurantId ||
            userData.restaurantId ||
            profile.restaurantId ||
            profile.restaurant_id ||
            (metadata as any).restaurantId ||
            (metadata as any).restaurant_id;
        const token = (user as any).token || userData.token || profile.token || profile.accessToken
            || (metadata as any).token || (metadata as any).accessToken;

        if (!restaurantId && !token) {
            return {
                status: "error",
                message: "I don't have your restaurant context right now. Please make sure you're logged in through the Mizan dashboard and try again."
            };
        }

        try {
            console.log(`[ScheduleOptimizerTool] Optimizing for ${restaurantId || 'from token'}, week: ${input.week_start}`);

            const result = await this.apiService.optimizeScheduleForAgent({
                restaurant_id: restaurantId || "",
                week_start: input.week_start,
                department: input.department
            }, token);

            const shiftsCount = result.shifts?.length ?? 0;
            const successMessage = result.message
                ? `Shifts have been scheduled successfully. ${result.message}`
                : `Shifts have been scheduled successfully. ${shiftsCount} shift(s) generated for the week of ${input.week_start}.`;

            return {
                status: "success",
                message: successMessage,
                shifts_generated: shiftsCount,
                metrics: result.optimization_metrics,
                schedule_summary: (result.shifts || []).map((s: any) => `${s.date} ${s.time}: ${s.staff} (${s.role || 'Staff'})`).slice(0, 10)
            };
        } catch (error: any) {
            console.error("[ScheduleOptimizerTool] Optimization failed:", error.message);
            const msg = error.message || "";
            if (msg === "TIMEOUT" || /timeout/i.test(msg)) {
                return {
                    status: "success",
                    message: "The schedule optimizer took longer than expected. Your shifts may have been created—please check your schedule in the dashboard. If you don't see them, try again in a few minutes."
                };
            }
            if (/restaurant context|resolve restaurant|Unable to resolve/i.test(msg)) {
                return {
                    status: "error",
                    message: "I couldn't access your restaurant settings for scheduling right now. Please try again in a moment, or make sure you're logged in through the Mizan dashboard."
                };
            }
            return {
                status: "error",
                message: `I wasn't able to optimize the schedule: ${msg}`
            };
        }
    }
}
