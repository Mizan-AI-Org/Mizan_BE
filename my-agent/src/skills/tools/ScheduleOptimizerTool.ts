import { LuaTool } from "lua-cli";
import { z } from "zod";

export default class ScheduleOptimizerTool implements LuaTool {
    name = "schedule_optimizer";
    description = "Optimize staff schedules based on predicted demand and staff availability.";

    inputSchema = z.object({
        week_start: z.string().describe("Start date of the week (YYYY-MM-DD)"),
        department: z.enum(["kitchen", "service", "all"]).optional(),
    });

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const restaurantId = context?.get ? context.get("restaurantId") : undefined;
        const restaurantName = context?.get ? context.get("restaurantName") : "Unknown Restaurant";

        if (!restaurantId) {
            // DEBUG: Inspect context to see what's actually there
            const keys = context ? Object.keys(context) : "null";
            // Check if context has a 'user' property directly
            const userProp = context?.user ? "present" : "missing";
            // Check if context has a 'traits' property directly
            const traitsProp = context?.traits ? "present" : "missing";

            return {
                status: "error",
                message: `No restaurant context found. Debug: Keys=[${keys}], User=${userProp}, Traits=${traitsProp}`
            };
        }

        console.log(`[ScheduleOptimizerTool] Executing for ${restaurantName} (${restaurantId})`);

        // Simulated logic
        return {
            status: "success",
            restaurant: restaurantName,
            message: `Schedule optimized for week of ${input.week_start} for ${restaurantName}`,
            insights: [
                "Increased kitchen staff on Friday evening due to expected tourist influx.",
                "Reduced service staff on Monday lunch based on historical low traffic."
            ],
            schedule_url: `https://mizan.ai/schedules/${restaurantId}/${input.week_start}`
        };
    }
}
