/**
 * Photo-to-action router tool.
 *
 * The manager (or staff) sends a photo on WhatsApp and Miya
 * 1. forwards the image URL to the backend `/agent/parse-photo/`
 *    endpoint, which runs OpenAI Vision and decides what kind of
 *    document/scene it is, then
 * 2. (when the classifier is confident enough) auto-creates the
 *    matching record — invoice, maintenance request, or incident —
 *    and returns the new record id together with a manager-facing
 *    summary.
 *
 * Categories the backend distinguishes today:
 *  - invoice_or_receipt
 *  - schedule
 *  - equipment_issue
 *  - incident
 *  - id_or_certification
 *  - inventory
 *  - other
 *
 * For ambiguous categories (`schedule`, `id_or_certification`,
 * `inventory`) we deliberately DON'T auto-create — instead the tool
 * returns a `next_step` so Miya can ask the manager exactly which
 * staff member / branch / shift the photo applies to.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError } from "./_common/errors";

function getRestaurantId(user: any) {
    const userData = user?.data || {};
    const profile = (user as any)?._luaProfile || {};
    return (
        (user as any)?.restaurantId ||
        userData.restaurantId ||
        profile.restaurantId ||
        (profile.metadata && (profile.metadata as any).restaurantId)
    );
}

const _photoApi = new ApiService();


export class ParsePhotoTool implements LuaTool {
    name = "parse_photo";
    description =
        "Classify any photo the manager sends and act on it: log invoices/receipts, open maintenance requests for broken equipment, file safety/health/security incidents, or hand off schedules and inventory shots to the right workflow. Use whenever the user attaches an image without explicitly telling you what to do with it (e.g. 'here\\'s the bill from the supplier', 'look at this leak', or just a bare photo). Always show the returned message_for_user verbatim so the manager knows what was logged.";

    inputSchema = z.object({
        imageUrl: z
            .string()
            .optional()
            .describe(
                "Public/temporary URL of the photo (typical for WhatsApp media). Either imageUrl or imageBase64 is required.",
            ),
        imageBase64: z
            .string()
            .optional()
            .describe(
                "Base64-encoded image bytes. Only use this if no URL is available — URLs are cheaper.",
            ),
        contentType: z
            .string()
            .optional()
            .describe("MIME type, e.g. 'image/jpeg'. Defaults to image/jpeg."),
        note: z
            .string()
            .optional()
            .describe(
                "Optional caption from the manager. Pass through anything they typed alongside the image (e.g. 'kitchen fridge has been like this since yesterday').",
            ),
        autoCreate: z
            .boolean()
            .optional()
            .describe(
                "Default true. Set to false to ONLY classify the photo without creating any record (useful for confirmation flows).",
            ),
        restaurantId: z
            .string()
            .optional()
            .describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
    });

    constructor(private apiService: ApiService = _photoApi) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid) return noContextError();

        if (!input.imageUrl && !input.imageBase64) {
            return {
                status: "error",
                message:
                    "I need the photo itself - either pass imageUrl (preferred) or imageBase64.",
            };
        }

        const data = await this.apiService.parsePhoto(rid, {
            imageUrl: input.imageUrl,
            imageBase64: input.imageBase64,
            contentType: input.contentType,
            note: input.note,
            autoCreate: input.autoCreate,
        });

        if (!data || data.success === false) {
            return {
                status: "error",
                message:
                    data?.message_for_user ||
                    data?.error ||
                    "I couldn't analyze that photo. Want to describe it instead?",
            };
        }

        const cls = data.classification || {};
        const action = data.action_taken || {};
        return {
            status: "success",
            // Verbatim line for Miya to surface in chat.
            message_for_user:
                action.message_for_user ||
                data.message_for_user ||
                cls.summary ||
                "Got the photo.",
            // Structured payload so Miya can chain follow-ups
            // (e.g. "want me to mark it paid now?" right after an invoice).
            classification: {
                category: cls.category,
                confidence: cls.confidence,
                summary: cls.summary,
                suggested_action: cls.suggested_action,
                fields: cls.fields || {},
            },
            action_taken: {
                type: action.type,
                record_id: action.record_id,
                priority: action.priority,
                category: action.category,
                invoice_status: action.invoice_status,
            },
        };
    }
}
