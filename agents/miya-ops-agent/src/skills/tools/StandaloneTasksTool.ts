/**
 * Standalone tasks: generate tasks from a template (due date + assignees) or run recurring task generation.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError } from "./_common/errors";

function getRestaurantId(user: any) {
    const userData = user?.data || {};
    const profile = (user as any)?._luaProfile || {};
    return (user as any)?.restaurantId || userData.restaurantId || profile.restaurantId || (profile.metadata && (profile.metadata as any).restaurantId);
}

export default class StandaloneTasksTool implements LuaTool {
    name = "standalone_tasks";
    description = "Create one-off tasks from a task template (with due date and optional assignees), or trigger recurring task generation for active templates. Use 'generate' when the manager says 'create tasks from template X for date Y' or 'assign template Z to Maria for Friday'. Use 'run_recurring' when they want to run today's recurring tasks (e.g. DAILY, WEEKLY).";

    inputSchema = z.object({
        action: z.enum(["generate", "run_recurring"]).describe("Generate tasks from a template, or run recurring generation."),
        template_id: z.string().optional().describe("Task template UUID (for generate)."),
        due_date: z.string().optional().describe("Due date YYYY-MM-DD (for generate)."),
        assigned_to: z.array(z.string()).optional().describe("Staff UUIDs to assign (for generate)."),
        frequency: z.string().optional().describe("Optional: DAILY, WEEKLY, MONTHLY (for run_recurring)."),
        date: z.string().optional().describe("Optional date YYYY-MM-DD for recurring run (for run_recurring)."),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid) return noContextError();

        if (input.action === "generate") {
            if (!input.template_id || !input.due_date) {
                return { status: "error", message: "template_id and due_date are required for generate." };
            }
            const result = await this.apiService.generateTasksFromTemplateForAgent(
                rid,
                input.template_id,
                input.due_date,
                input.assigned_to
            );
            if (!result.success) return { status: "error", message: result.error };
            return { status: "success", message: result.message, tasks_created: result.tasks_created, tasks: result.tasks };
        }

        if (input.action === "run_recurring") {
            const result = await this.apiService.runRecurringTasksForAgent(rid, {
                frequency: input.frequency,
                date: input.date,
            });
            if (!result.success) return { status: "error", message: result.error };
            return { status: "success", message: result.message, tasks_created: result.tasks_created };
        }

        return { status: "error", message: "Invalid action." };
    }
}
