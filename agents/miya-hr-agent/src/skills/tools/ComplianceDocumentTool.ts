/**
 * ComplianceDocumentTool — restaurant permits & certificates with expiry reminders.
 * Insurance, hygiene, fire extinguishers, business registration, etc.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError, upstreamError, validationError } from "./_common/errors";

export default class ComplianceDocumentTool implements LuaTool {
    name = "compliance_documents";
    description =
        "Track restaurant compliance documents that expire (business registration, insurance, " +
        "hygiene/food-safety certificates, fire extinguisher inspections, health permits). " +
        "Use action='list' to see what's expiring. action='seed' to add suggested document types. " +
        "action='record' to add a document with an expiry date. action='update' to set/change expiry. " +
        "Miya reminds owners/managers automatically before dates lapse.";

    inputSchema = z.object({
        action: z.enum(["list", "seed", "record", "update"]),
        title: z.string().optional().describe("Required for record. Document name."),
        document_type: z
            .enum([
                "BUSINESS_REGISTRATION",
                "INSURANCE",
                "FIRE_EXTINGUISHER",
                "HYGIENE",
                "HEALTH_PERMIT",
                "LIQUOR_LICENSE",
                "EQUIPMENT_INSPECTION",
                "OTHER",
            ])
            .optional(),
        expires_at: z.string().optional().describe("YYYY-MM-DD expiry date"),
        remind_days_before: z.number().min(1).max(365).optional(),
        description: z.string().optional(),
        reference_number: z.string().optional(),
        id: z.string().optional().describe("Document id — required for update"),
        expiring_within_days: z
            .number()
            .min(0)
            .max(365)
            .optional()
            .describe("For list: focus on docs due within N days (default 90)"),
        restaurantId: z
            .string()
            .optional()
            .describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return noContextError();
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};
        const rid: string | undefined =
            input.restaurantId ||
            (user as any).restaurantId ||
            userData.restaurantId ||
            profile.restaurantId;
        if (!rid) return noContextError();

        if (input.action === "list") {
            const res = await this.apiService.listComplianceDocumentsForAgent(rid, {
                expiring_within_days: input.expiring_within_days ?? 90,
            });
            if (res && res.success === false) return upstreamError(res.error);
            return {
                status: "success",
                count: res?.count || 0,
                expired: res?.expired || 0,
                expiring_soon: res?.expiring_soon || 0,
                missing_date: res?.missing_date || 0,
                documents: res?.documents || [],
                message: res?.message_for_user || `${res?.count || 0} document(s).`,
                miya_directive:
                    "Summarise clearly for the manager. Highlight expired and due-soon items. " +
                    "Offer to set missing expiry dates. Never invent dates.",
            };
        }

        if (input.action === "seed") {
            const res = await this.apiService.seedComplianceDocumentsForAgent(rid);
            if (res && res.success === false) return upstreamError(res.error);
            return {
                status: "success",
                created: res?.created || 0,
                documents: res?.documents || [],
                message: res?.message_for_user || "Starter documents ready.",
                miya_directive: "Ask the manager for expiry dates so reminders can start.",
            };
        }

        if (input.action === "update") {
            if (!input.id) return validationError("Provide the document id to update.");
            const res = await this.apiService.updateComplianceDocumentForAgent({
                restaurant_id: rid,
                id: input.id,
                title: input.title,
                document_type: input.document_type,
                expires_at: input.expires_at,
                remind_days_before: input.remind_days_before,
                description: input.description,
                reference_number: input.reference_number,
            });
            if (res && res.success === false) return upstreamError(res.error);
            return {
                status: "success",
                document: res?.document,
                message: res?.message_for_user || "Document updated.",
            };
        }

        // record
        if (!input.title?.trim()) return validationError("title is required.");
        const res = await this.apiService.createComplianceDocumentForAgent({
            restaurant_id: rid,
            title: input.title.trim(),
            document_type: input.document_type,
            expires_at: input.expires_at,
            remind_days_before: input.remind_days_before,
            description: input.description,
            reference_number: input.reference_number,
        });
        if (res && res.success === false) return upstreamError(res.error);
        return {
            status: "success",
            document: res?.document,
            message: res?.message_for_user || "Document recorded.",
        };
    }
}
