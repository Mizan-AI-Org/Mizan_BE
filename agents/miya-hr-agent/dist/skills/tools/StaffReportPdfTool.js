/**
 * Generate staff profile report PDF (hours, contact, employment). For payroll or manager reference.
 */
import { User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError } from "./_common/errors";
function getRestaurantId(user) {
    const userData = user?.data || {};
    const profile = user?._luaProfile || {};
    return user?.restaurantId || userData.restaurantId || profile.restaurantId || (profile.metadata && profile.metadata.restaurantId);
}
export default class StaffReportPdfTool {
    constructor(apiService = new ApiService()) {
        this.apiService = apiService;
        this.name = "staff_report_pdf";
        this.description = "Generate the staff profile report (PDF) for a staff member: hours summary, contact, employment details. Use when the manager asks for 'hours report for [name]', 'staff report for payroll', or 'generate report for [staff]'. Provide staff_id (UUID); use staff_lookup first if you only have a name.";
        this.inputSchema = z.object({
            staff_id: z.string().describe("Staff member UUID."),
            restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
        });
    }
    async execute(input) {
        const user = await User.get();
        if (!user)
            return { status: "error", message: "No context." };
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid)
            return noContextError();
        const result = await this.apiService.getStaffReportPdfForAgent(rid, input.staff_id);
        if (!result.success)
            return { status: "error", message: result.error };
        return {
            status: "success",
            message: `Staff report PDF has been generated. The manager can download it from Dashboard > Staff > [staff] > Report PDF, or use the same staff in the dashboard.`,
            filename: result.filename,
        };
    }
}
