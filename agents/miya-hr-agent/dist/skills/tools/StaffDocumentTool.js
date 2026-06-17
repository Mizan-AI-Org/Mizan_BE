/**
 * StaffDocumentTool — list or record staff documents (contracts, IDs, certifications).
 * Useful across verticals (healthcare CE certificates, construction PPE training,
 * restaurant food-handler licences, etc.).
 *
 * NOTE: Current StaffDocument model stores title + file only. This tool accepts
 * forward-compatible fields (document_type, notes, expires_at) that the backend
 * will save only if the model exposes them.
 */
import { User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError, upstreamError, validationError } from "./_common/errors";
export default class StaffDocumentTool {
    constructor(apiService = new ApiService()) {
        this.apiService = apiService;
        this.name = "staff_documents";
        this.description = "List or record staff documents (contracts, IDs, licences, certifications). " +
            "Use action='list' to view documents — supports expiring_within_days for reminders. " +
            "Use action='record' to log a new document title for a staff member. " +
            "File uploads still go through the dashboard; this tool tracks titles/metadata.";
        this.inputSchema = z.object({
            action: z.enum(["list", "record"]),
            staff_id: z.string().optional(),
            phone: z.string().optional(),
            title: z.string().optional().describe("Required for action='record'."),
            document_type: z.string().optional().describe("E.g. 'CONTRACT', 'ID', 'LICENCE', 'CERTIFICATE', 'OTHER'."),
            notes: z.string().optional(),
            expires_at: z.string().optional().describe("ISO datetime for expiring documents."),
            expiring_within_days: z.number().min(0).max(365).optional().describe("For 'list': only show docs expiring within N days."),
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
            const res = await this.apiService.listStaffDocumentsForAgent(rid, {
                staff_id: input.staff_id,
                expiring_within_days: input.expiring_within_days,
            });
            if (res && res.success === false)
                return upstreamError(res.error);
            return {
                status: "success",
                count: res?.count || 0,
                documents: res?.documents || [],
                message: (res?.count || 0) === 0
                    ? "No documents match that filter."
                    : `${res.count} document(s).`,
                miya_directive: "Summarise in the user's language. Highlight any document expiring soon.",
            };
        }
        // record
        if (!input.title || !input.title.trim())
            return validationError("title is required.");
        if (!input.staff_id && !input.phone)
            return validationError("Provide staff_id or phone.");
        const res = await this.apiService.createStaffDocumentForAgent({
            restaurant_id: rid,
            title: input.title.trim(),
            staff_id: input.staff_id,
            phone: input.phone,
            document_type: input.document_type,
            notes: input.notes,
            expires_at: input.expires_at,
        });
        if (res && res.success === false)
            return upstreamError(res.error);
        return {
            status: "success",
            document_id: res?.document_id,
            message: res?.message || "Document recorded.",
        };
    }
}
