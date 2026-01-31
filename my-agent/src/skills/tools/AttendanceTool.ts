import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class AttendanceTool implements LuaTool {
    name = "get_attendance_report";
    description = "Get a report of who is on duty today, who has clocked in, and who is late. Use this to respond to manager queries about staff punctuality and presence.";

    inputSchema = z.object({
        date: z.string().optional().describe("The date to check (YYYY-MM-DD). Defaults to today."),
        restaurantId: z.string().optional().describe("Restaurant ID from context")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        // Resolve restaurantId
        const user = await User.get();
        const userData = user ? ((user as any).data || {}) : {};
        const profile = user ? ((user as any)._luaProfile || {}) : {};

        let restaurantId =
            input.restaurantId ||
            (user as any)?.restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;

        if (!restaurantId) {
            return {
                status: "error",
                message: "I need to know which restaurant this is for. Please make sure I have the restaurant context."
            };
        }

        try {
            console.log(`[AttendanceTool] Fetching attendance report for restaurant ${restaurantId} on date ${input.date || 'today'}`);
            const data = await this.apiService.getAttendanceReport(restaurantId, input.date);

            if (!data.summary || data.summary.length === 0) {
                return {
                    status: "success",
                    message: `No staff are scheduled for ${data.date || 'today'}.`,
                    report: []
                };
            }

            const lateStaff = data.summary.filter((s: any) => s.status === "Late");
            const presentStaff = data.summary.filter((s: any) => s.status === "Present" || s.status === "Late");
            const missingStaff = data.summary.filter((s: any) => s.status === "Missing");
            const scheduledCount = data.summary.length;

            let message = `For ${data.date}, there are ${scheduledCount} staff members scheduled. `;
            message += `${presentStaff.length} are present (${lateStaff.length} late). `;
            if (missingStaff.length > 0) {
                message += `${missingStaff.length} have not clocked in yet despite their shift having started.`;
            }

            return {
                status: "success",
                message: message,
                report: data.summary,
                stats: {
                    scheduled: scheduledCount,
                    present: presentStaff.length,
                    late: lateStaff.length,
                    missing: missingStaff.length
                }
            };

        } catch (error: any) {
            console.error("[AttendanceTool] Execution failed:", error.message);
            return {
                status: "error",
                message: `Failed to fetch attendance report: ${error.message}`
            };
        }
    }
}
