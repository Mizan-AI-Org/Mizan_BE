/**
 * Generate staff profile report PDF (hours, contact, employment). For payroll or manager reference.
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

export default class StaffReportPdfTool implements LuaTool {
    name = "staff_report_pdf";
    description = "Generate the staff profile report (PDF) for a staff member: hours summary, contact, employment details. Use when the manager asks for 'hours report for [name]', 'staff report for payroll', or 'generate report for [staff]'. Provide staff_id (UUID); use staff_lookup first if you only have a name.";

    inputSchema = z.object({
        staff_id: z.string().describe("Staff member UUID."),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid) return noContextError();

        const result = await this.apiService.getStaffReportPdfForAgent(rid, input.staff_id);
        if (!result.success) return { status: "error", message: result.error };
        return {
            status: "success",
            message: `Staff report PDF has been generated. The manager can download it from Dashboard > Staff > [staff] > Report PDF, or use the same staff in the dashboard.`,
            filename: result.filename,
        };
    }
}
