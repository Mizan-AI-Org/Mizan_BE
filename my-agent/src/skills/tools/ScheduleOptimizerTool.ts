import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class ScheduleOptimizerTool implements LuaTool {
    name = "schedule_optimizer";
    description = "Optimize staff schedules based on predicted demand and staff availability.";

    inputSchema = z.object({
        week_start: z.string().describe("Start date of the week (YYYY-MM-DD)"),
        department: z.enum(["kitchen", "service", "all"]).optional().describe("Department to optimize"),
        restaurantId: z.string().optional().describe("Restaurant ID (will use context if not provided)")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        // DEBUG: Log the entire context to diagnose missing restaurantId
        console.log('[ScheduleOptimizerTool] Full context received:', JSON.stringify(context, null, 2));
        console.log('[ScheduleOptimizerTool] Metadata:', context?.metadata);
        console.log('[ScheduleOptimizerTool] Input:', input);

        // Priority: input parameter > context.get > context.metadata > context direct
        const restaurantId =
            input.restaurantId ||
            (context?.get ? context.get("restaurantId") : undefined) ||
            context?.metadata?.restaurantId ||
            context?.restaurantId;

        const token = context?.metadata?.token || (context?.get ? context.get("token") : undefined);

        if (!restaurantId) {
            console.error('[ScheduleOptimizerTool] Context keys:', context ? Object.keys(context) : 'null');
            console.error('[ScheduleOptimizerTool] Input keys:', Object.keys(input));
            return {
                status: "error",
                message: "Restaurant context is missing. Please provide restaurantId in the request or ensure your session is authenticated."
            };
        }

        if (!token) {
            return {
                status: "error",
                message: "Authentication token missing. Cannot access scheduling API."
            };
        }

        const userToken = token || context?.user?.token;

        try {
            console.log(`[ScheduleOptimizerTool] Optimizing for ${restaurantId}, week: ${input.week_start}`);

            const result = await this.apiService.optimizeSchedule({
                week_start: input.week_start,
                department: input.department
            }, userToken);

            return {
                status: "success",
                message: result.message,
                shifts_generated: result.shifts.length,
                metrics: result.optimization_metrics,
                schedule_summary: result.shifts.map((s: any) => `${s.date} ${s.time}: ${s.staff} (${s.role || 'Staff'})`).slice(0, 10) // Limit summary
            };
        } catch (error: any) {
            console.error("[ScheduleOptimizerTool] Optimization failed:", error.message);
            return {
                status: "error",
                message: `Optimization failed: ${error.message}`
            };
        }
    }
}
