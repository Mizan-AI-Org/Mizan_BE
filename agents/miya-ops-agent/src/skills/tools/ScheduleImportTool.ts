/**
 * Schedule import from photo or document: parse then apply to a week or save as template.
 * Use when the manager sends a schedule photo/document and wants to import it.
 */
import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError } from "./_common/errors";

function getTokenAndRestaurant(user: any) {
    const userData = user?.data || {};
    const profile = (user as any)?._luaProfile || {};
    const token =
        env("LUA_WEBHOOK_API_KEY") ||
        env("WEBHOOK_API_KEY") ||
        user?.token ||
        userData.token ||
        profile.token ||
        profile.accessToken ||
        env("MIZAN_SERVICE_TOKEN");
    const restaurantId =
        (user as any)?.restaurantId || userData.restaurantId || profile.restaurantId || (profile.metadata && (profile.metadata as any).restaurantId);
    return { token, restaurantId };
}

export default class ScheduleImportTool implements LuaTool {
    name = "schedule_import";
    description = "Import a schedule from a photo or document. Use 'parse_photo' when the manager sends a schedule image (pass base64_image). Use 'parse_document' for Excel/CSV (pass base64_content and filename). Use 'apply' to create shifts from a previous parse result (pass shifts array, and either week_start YYYY-MM-DD to apply to that week, or save_as_template true to save as template only). Always use restaurant context.";

    inputSchema = z.object({
        action: z.enum(["parse_photo", "parse_document", "apply"]).describe("Parse a photo, parse a document, or apply a previously parsed schedule."),
        base64_image: z.string().optional().describe("Base64-encoded image (for parse_photo)."),
        base64_content: z.string().optional().describe("Base64-encoded file content (for parse_document)."),
        filename: z.string().optional().describe("Filename e.g. schedule.xlsx or schedule.csv (for parse_document)."),
        content_type: z.string().optional().describe("Image content type e.g. image/jpeg (for parse_photo)."),
        template_name: z.string().optional().describe("Name for the schedule (for apply)."),
        shifts: z.array(z.object({
            employee_name: z.string().optional(),
            role: z.string().optional(),
            department: z.string().optional(),
            day_of_week: z.number().min(0).max(6),
            start_time: z.string().optional(),
            end_time: z.string().optional(),
        })).optional().describe("Parsed shifts from parse_photo or parse_document (for apply)."),
        save_as_template: z.boolean().optional().describe("If true, save as schedule template only (for apply)."),
        week_start: z.string().optional().describe("Week start date YYYY-MM-DD to create shifts for that week (for apply)."),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return noContextError();
        const { restaurantId } = getTokenAndRestaurant(user);
        const rid = input.restaurantId || restaurantId;
        if (!rid) return noContextError();

        if (input.action === "parse_photo") {
            if (!input.base64_image) return { status: "error", message: "base64_image required for parse_photo." };
            const result = await this.apiService.parseSchedulePhotoForAgent(input.base64_image, input.content_type, rid);
            if (result.error) return { status: "error", message: result.error, shifts: result.shifts };
            return {
                status: "success",
                template_name: result.template_name,
                shifts: result.shifts,
                message: `Parsed ${(result.shifts || []).length} shift(s). Use action='apply' with these shifts and week_start or save_as_template to create the schedule.`,
            };
        }

        if (input.action === "parse_document") {
            if (!input.base64_content || !input.filename) return { status: "error", message: "base64_content and filename required for parse_document." };
            const result = await this.apiService.parseScheduleDocumentForAgent(input.base64_content, input.filename, rid);
            if (result.error) return { status: "error", message: result.error, shifts: result.shifts };
            return {
                status: "success",
                template_name: result.template_name,
                shifts: result.shifts,
                message: `Parsed ${(result.shifts || []).length} shift(s). Use action='apply' with these shifts and week_start or save_as_template to create the schedule.`,
            };
        }

        if (input.action === "apply") {
            if (!input.shifts || !Array.isArray(input.shifts) || input.shifts.length === 0) {
                return { status: "error", message: "shifts array required for apply. Use parse_photo or parse_document first." };
            }
            if (!input.week_start && !input.save_as_template) {
                return { status: "error", message: "Provide week_start (YYYY-MM-DD) to create shifts for a week, or save_as_template true to save as template only." };
            }
            const result = await this.apiService.applyParsedScheduleForAgent(rid, {
                template_name: input.template_name,
                shifts: input.shifts,
                save_as_template: !!input.save_as_template,
                week_start: input.week_start,
            });
            if (!result.success) return { status: "error", message: result.error };
            return {
                status: "success",
                message: result.message || "Schedule applied.",
                template: result.template,
                applied_shift_ids: result.applied_shift_ids,
            };
        }

        return { status: "error", message: "Invalid action." };
    }
}
