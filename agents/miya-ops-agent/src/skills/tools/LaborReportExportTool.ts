/**
 * Generate labor/attendance report export (PDF or Excel) for HR/payroll.
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

export default class LaborReportExportTool implements LuaTool {
    name = "labor_report_export";
    description = "Generate the staff attendance/labor report for a date range (PDF or Excel) for HR or payroll. Use when the manager asks for 'labor report', 'attendance export', or 'payroll report'. Provide start_date and end_date (YYYY-MM-DD) and format (pdf or excel). The report is generated; in WhatsApp you can tell the manager to open Dashboard > Reports > Attendance and export with the same dates, or that the report is ready.";

    inputSchema = z.object({
        start_date: z.string().describe("Start date YYYY-MM-DD."),
        end_date: z.string().describe("End date YYYY-MM-DD."),
        format: z.enum(["pdf", "excel", "xlsx"]).optional().default("excel").describe("Export format: pdf or excel."),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid) return noContextError();

        const result = await this.apiService.getAttendanceExportForAgent(
            rid,
            input.start_date,
            input.end_date,
            input.format === "xlsx" ? "xlsx" : input.format as "pdf" | "excel"
        );
        if (!result.success) return { status: "error", message: result.error };
        return {
            status: "success",
            message: `Labor report (${input.format}) for ${input.start_date} to ${input.end_date} has been generated. Open the Mizan dashboard, go to Reports > Attendance, select the same dates, and click Export to download "${result.filename}".`,
            filename: result.filename,
        };
    }
}
