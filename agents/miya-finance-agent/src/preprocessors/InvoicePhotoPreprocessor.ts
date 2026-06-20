/**
 * When staff send an invoice photo on WhatsApp, run parse_photo immediately
 * (server-side media fetch + vision) instead of leaving it to the LLM — which
 * often fails to download Meta URLs or save human-readable dates.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import { ensureDashboardWidget } from "./executeOperationsIntent";
import { extractMessageText } from "../utils/extractLastUserText";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";

const INVOICE_PAY_RE =
    /\b(invoice|facture|bill|receipt|payable|accounts?\s*payable|payer|pay|payment|khlass|facture|à payer|a payer|we have to pay|need to pay|must pay|faut payer|enregistr|log.*invoice|record.*invoice)\b/i;

function collectRecentText(messages: ChatMessage[], limit = 8): string[] {
    const out: string[] = [];
    for (const msg of messages.slice(-limit)) {
        const t = extractMessageText(msg);
        if (t) out.push(t);
    }
    return out;
}

function extractImageUrl(messages: ChatMessage[]): string | null {
    for (const msg of messages) {
        if (msg.type === "image") {
            const url = (msg as { image?: string }).image;
            if (url) return String(url);
        }
        if (msg.type === "file") {
            const mime = String((msg as { mimeType?: string }).mimeType || "").toLowerCase();
            if (mime.startsWith("image/")) {
                const url = (msg as { data?: string }).data;
                if (url) return String(url);
            }
        }
    }
    return null;
}

function looksLikeInvoiceSubmission(messages: ChatMessage[]): boolean {
    const texts = collectRecentText(messages);
    if (texts.length === 0) {
        // Photo-only: still try — vision classifier decides.
        return messages.some((m) => m.type === "image" || m.type === "file");
    }
    const joined = texts.join("\n");
    return INVOICE_PAY_RE.test(joined);
}

function staffFacingMessage(data: Record<string, unknown>, fallback: string): string {
    const action = (data.action_taken || {}) as Record<string, unknown>;
    return String(
        action.message_for_user ||
            data.message_for_user ||
            fallback,
    ).trim();
}

export const invoicePhotoPreprocessor = new PreProcessor({
    name: "invoice-photo-router",
    description:
        "Auto-parse invoice photos from WhatsApp and log them to the Finance dashboard widget.",
    priority: 106,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const imageUrl = extractImageUrl(messages);
        if (!imageUrl) {
            return { action: "proceed" as const };
        }
        if (!looksLikeInvoiceSubmission(messages)) {
            return { action: "proceed" as const };
        }

        const tenant = await resolveTenantForUser(user);
        const restaurantId = tenant.restaurantId;
        if (!restaurantId) {
            return { action: "proceed" as const };
        }

        const note = collectRecentText(messages).join("\n").slice(0, 500);
        const api = new ApiService();

        console.log(
            `[InvoicePhotoPreprocessor] parsing invoice photo channel=${channel} restaurant=${restaurantId}`,
        );

        let data: Record<string, unknown>;
        try {
            data = (await api.parsePhoto(restaurantId, {
                imageUrl,
                note,
                autoCreate: true,
            })) as Record<string, unknown>;
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[InvoicePhotoPreprocessor] parsePhoto threw:", em);
            return { action: "proceed" as const };
        }

        if (data?.success === false) {
            const msg = staffFacingMessage(
                data,
                "I couldn't read that invoice photo. Please resend it or type the vendor, amount, and due date.",
            );
            return { action: "block" as const, response: msg };
        }

        const action = (data.action_taken || {}) as Record<string, unknown>;
        const actionType = String(action.type || "");
        const recordId = action.record_id ? String(action.record_id) : "";

        if (actionType === "invoice" && recordId) {
            await ensureDashboardWidget(api, "finance", restaurantId, {
                userId: tenant.userId,
                email: tenant.email,
                phone: tenant.phone,
            });
            return {
                action: "block" as const,
                response: staffFacingMessage(
                    data,
                    "✓ Invoice logged for the manager's Finance dashboard.",
                ),
                metadata: { invoice_id: recordId, widget: "finance" },
            };
        }

        if (actionType === "invoice_pending") {
            const cls = (data.classification || {}) as Record<string, unknown>;
            const fields = (cls.fields || {}) as Record<string, unknown>;
            const vendor = String(fields.vendor || "").trim();
            const amountRaw = fields.amount;
            const amount =
                typeof amountRaw === "number"
                    ? amountRaw
                    : Number(String(amountRaw || "").replace(/[^\d.]/g, ""));
            const dueDate = String(fields.due_date || "").trim();
            if (vendor && Number.isFinite(amount) && amount > 0 && dueDate) {
                const recorded = await api.recordInvoice(restaurantId, {
                    vendor,
                    amount,
                    due_date: dueDate,
                    invoice_number: fields.invoice_number
                        ? String(fields.invoice_number)
                        : undefined,
                    currency: fields.currency ? String(fields.currency) : undefined,
                    notes: note,
                    photo_url: imageUrl,
                });
                if (recorded?.success !== false && recorded?.invoice?.id) {
                    await ensureDashboardWidget(api, "finance", restaurantId, {
                        userId: tenant.userId,
                        email: tenant.email,
                        phone: tenant.phone,
                    });
                    return {
                        action: "block" as const,
                        response:
                            recorded.message_for_user ||
                            `✓ Logged invoice from ${vendor} on the manager's Finance dashboard.`,
                        metadata: {
                            invoice_id: recorded.invoice.id,
                            widget: "finance",
                        },
                    };
                }
            }
            return {
                action: "block" as const,
                response: staffFacingMessage(
                    data,
                    "I read the invoice but need the vendor, amount, and due date confirmed before I can log it.",
                ),
            };
        }

        // Not an invoice photo — let the normal agent handle (maintenance, etc.).
        return { action: "proceed" as const };
    },
});

export default invoicePhotoPreprocessor;
