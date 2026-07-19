/**
 * Document-to-action router tool.
 *
 * Sibling of ParsePhotoTool for non-image attachments. The manager
 * sends a PDF / Word / Excel / CSV / TXT file, Miya forwards it to
 * `/agent/parse-document/`, the backend extracts text and classifies
 * the document, and (when confidence is high) auto-creates the
 * matching record (today: invoice).
 *
 * HARD GUARDRAILS (also enforced server-side):
 *  - This tool only accepts NON-IMAGE attachments. For images call
 *    parse_photo instead.
 *  - When the backend returns `status: "wrong_tool"` /
 *    `status: "unsupported"` / `status: "empty"` Miya MUST follow
 *    `miya_directive` and never claim to have logged anything.
 *  - When `action_taken.type === "low_confidence"` or
 *    `action_taken.type === "invoice_pending"` Miya MUST ask the user
 *    for the missing fields rather than fabricating them.
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

const _docApi = new ApiService();


export class ParseDocumentTool implements LuaTool {
    name = "parse_document";
    description =
        "Read a non-image document the manager attached (PDF, Word/.docx, Excel/.xlsx, CSV, plain text) and act on it. Use this whenever the user uploads ANY non-image file — do NOT call parse_photo on docs. The backend extracts text and classifies the doc; if it's a supplier invoice with confident vendor/amount/due_date it logs it automatically. If the manager wants Processes & Tasks recreated from the file, set importProcesses=true (or mention processes/checklists in note). NEVER fabricate vendor / amount / invoice_number / due_date / issue_date.";

    inputSchema = z.object({
        documentUrl: z
            .string()
            .optional()
            .describe(
                "Public/temporary URL of the file (typical for WhatsApp media). Either documentUrl or documentBase64 is required.",
            ),
        documentBase64: z
            .string()
            .optional()
            .describe(
                "Base64-encoded file bytes. Only use if no URL is available.",
            ),
        contentType: z
            .string()
            .optional()
            .describe(
                "MIME type of the file, e.g. 'application/pdf' or 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'. Pass it through verbatim — do NOT pass image/* MIME types here.",
            ),
        fileName: z
            .string()
            .optional()
            .describe(
                "Original filename if you have it (e.g. 'invoice-04-2021.docx'). Helps the backend pick the right extractor.",
            ),
        note: z
            .string()
            .optional()
            .describe(
                "Optional caption from the manager. Pass through anything they typed alongside the file.",
            ),
        autoCreate: z
            .boolean()
            .optional()
            .describe(
                "Default true. Set to false to ONLY classify the document without creating any record.",
            ),
        importProcesses: z
            .boolean()
            .optional()
            .describe(
                "Set true when the manager wants Processes & Tasks checklists imported from this file.",
            ),
        restaurantId: z
            .string()
            .optional()
            .describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
    });

    constructor(private apiService: ApiService = _docApi) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid) return noContextError();

        if (!input.documentUrl && !input.documentBase64) {
            return {
                status: "error",
                code: "VALIDATION",
                message: "I need the document itself — either documentUrl (preferred) or documentBase64.",
                miya_directive:
                    "Ask the user to re-attach the file, or transcribe its key fields and call record_invoice manually.",
            };
        }

        // Refuse to forward images to this endpoint — that's parse_photo's job.
        const ct = (input.contentType || "").toLowerCase();
        if (ct.startsWith("image/")) {
            return {
                status: "wrong_tool",
                code: "USE_PARSE_PHOTO",
                message: "parse_document is for non-image files only.",
                miya_directive:
                    "Re-route this attachment: call parse_photo with the same URL/bytes. Do NOT pretend you parsed the file.",
            };
        }

        const data = await this.apiService.parseDocument(rid, {
            documentUrl: input.documentUrl,
            documentBase64: input.documentBase64,
            contentType: input.contentType,
            fileName: input.fileName,
            note: input.note,
            autoCreate: input.autoCreate,
            importProcesses: input.importProcesses,
        });

        // Backend uses 4xx for "we read it but the classifier needs help" cases —
        // pass the directive through so Miya can recover.
        if (!data) {
            return {
                status: "error",
                code: "UPSTREAM",
                message: "Document parser is unavailable right now.",
            };
        }

        if (data.success === false) {
            const code: string | undefined = data.code;
            const directive: string | undefined = data.miya_directive;
            const userMsg: string | undefined = data.message_for_user;

            if (code === "USE_PARSE_PHOTO" || data.status === "wrong_tool") {
                return {
                    status: "wrong_tool",
                    code: code || "USE_PARSE_PHOTO",
                    message: data.error || "Wrong tool for this file.",
                    miya_directive: directive,
                };
            }
            if (code === "UNSUPPORTED_DOCUMENT_TYPE" || code === "EMPTY_DOCUMENT") {
                return {
                    status: "needs_user_input",
                    code,
                    message: userMsg || data.error || "Couldn't read the document.",
                    miya_directive:
                        directive ||
                        "Ask the user to type out the key fields (vendor, amount, due_date, invoice_number) and call record_invoice with the values they confirm. NEVER invent fields.",
                    classification: data.classification || {},
                };
            }
            return {
                status: "error",
                code: code || "UPSTREAM",
                message: userMsg || data.error || "Document parser failed.",
            };
        }

        const cls = data.classification || {};
        const action = data.action_taken || {};
        const fields = cls.fields || {};
        const lowConf = action.type === "low_confidence" || action.type === "invoice_pending";
        const processImported = action.type === "process_templates_imported";

        return {
            status: lowConf ? "needs_user_input" : "success",
            // Verbatim line for Miya to surface in chat — safe because the backend
            // already filtered out hallucinated values (anything missing is null).
            message_for_user:
                action.message_for_user ||
                data.message_for_user ||
                cls.summary ||
                "Got the document.",
            classification: {
                category: cls.category,
                confidence: cls.confidence,
                summary: cls.summary,
                suggested_action: cls.suggested_action,
                extracted_kind: cls.extracted_kind,
                extracted_chars: cls.extracted_chars,
                fields: {
                    vendor: fields.vendor || null,
                    amount: fields.amount ?? null,
                    currency: fields.currency || null,
                    invoice_number: fields.invoice_number || null,
                    due_date: fields.due_date || null,
                    issue_date: fields.issue_date || null,
                    document_type: fields.document_type || null,
                    person_name: fields.person_name || null,
                    expiry_date: fields.expiry_date || null,
                    title: fields.title || null,
                },
            },
            action_taken: {
                type: action.type,
                record_id: action.record_id,
                invoice_status: action.invoice_status,
                created_count: action.created_count,
                created_templates: action.created_templates,
            },
            miya_directive: processImported
                ? "Processes were imported — confirm the count and names from message_for_user. Do NOT claim success without created_templates."
                : lowConf
                ? "The classifier was not confident enough or required fields were missing. Tell the user briefly what you read, ask for the missing fields (vendor / amount / due_date / invoice_number), and then call record_invoice. NEVER fabricate values."
                : undefined,
        };
    }
}
