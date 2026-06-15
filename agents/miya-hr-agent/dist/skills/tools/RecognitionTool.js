/**
 * RecognitionTool — give/list kudos or safety recognitions.
 * Actions:
 *   - award: recognise a staff member (title, optional description, points, type)
 *   - list: fetch recent recognitions for a restaurant or a specific staff member
 */
import { User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError, upstreamError, validationError } from "./_common/errors";
export default class RecognitionTool {
    constructor(apiService = new ApiService()) {
        this.apiService = apiService;
        this.name = "recognize_staff";
        this.description = "Give a public recognition / kudos / safety recognition to a staff member, or list recent ones. " +
            "Use for 'shout out to X', 'give kudos', 'recognise X for Y', 'félicitations à', 'شكر خاص'. " +
            "action='award' requires title and staff_id OR phone OR staff_name. " +
            "action='list' returns recent recognitions (optionally filtered by staff_id).";
        this.inputSchema = z.object({
            action: z.enum(["award", "list"]),
            title: z.string().optional().describe("Required for 'award' (e.g. 'Great service', 'Safety Champion')."),
            description: z.string().optional(),
            recognition_type: z.string().optional().describe("Default 'Kudos'. Examples: 'Safety Champion', 'Hazard Spotter', 'Customer Hero'."),
            points: z.number().optional().describe("Gamification points, default 0."),
            staff_id: z.string().optional(),
            phone: z.string().optional(),
            staff_name: z.string().optional(),
            awarded_by_phone: z.string().optional(),
            awarded_by_user_id: z.string().optional(),
            days: z.number().min(1).max(365).optional().describe("For 'list'. Default 30."),
            limit: z.number().min(1).max(100).optional(),
            restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
        });
    }
    async execute(input) {
        const user = await User.get();
        if (!user)
            return noContextError();
        const userData = user.data || {};
        const profile = user._luaProfile || {};
        const rid = input.restaurantId ||
            user.restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;
        if (!rid)
            return noContextError();
        if (input.action === "list") {
            const res = await this.apiService.listRecognitionsForAgent(rid, {
                days: input.days,
                staff_id: input.staff_id,
                limit: input.limit,
            });
            if (res && res.success === false)
                return upstreamError(res.error);
            const count = res?.count || 0;
            return {
                status: "success",
                count,
                recognitions: res?.recognitions || [],
                message: count === 0 ? "No recognitions yet in that window." : `${count} recognition(s).`,
                miya_directive: "Summarise in the user's language. List the recipients + titles compactly.",
            };
        }
        // award
        if (!input.title || !input.title.trim())
            return validationError("title is required.");
        if (!input.staff_id && !input.phone && !input.staff_name) {
            return validationError("Provide staff_id, phone, or staff_name.");
        }
        const res = await this.apiService.recognizeStaffForAgent({
            restaurant_id: rid,
            title: input.title.trim(),
            description: input.description,
            recognition_type: input.recognition_type,
            points: input.points,
            staff_id: input.staff_id,
            phone: input.phone,
            staff_name: input.staff_name,
            awarded_by_phone: input.awarded_by_phone,
            awarded_by_user_id: input.awarded_by_user_id,
        });
        if (res && res.success === false)
            return upstreamError(res.error);
        return {
            status: "success",
            recognition_id: res?.recognition_id,
            staff_id: res?.staff_id,
            staff_name: res?.staff_name,
            title: res?.title,
            points: res?.points,
            message: res?.message || "Recognition awarded.",
            miya_directive: "Confirm in the user's language (warm, short). Mention recipient + title.",
        };
    }
}
