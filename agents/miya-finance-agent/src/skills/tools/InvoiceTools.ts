/**
 * Finance / Accounts Payable tools.
 *
 * Three sibling tools that share an ApiService instance:
 *   - record_invoice      — create a bill the restaurant owes
 *   - mark_invoice_paid   — flip a bill to PAID
 *   - list_invoices       — read open / overdue / due-soon invoices
 *
 * Designed for chat-driven usage: every tool returns a
 * ``message_for_user`` line Miya can quote verbatim, plus structured
 * ``invoice``/``invoices`` payloads if the agent needs to follow up
 * (e.g. "ok, paying it now").
 *
 * The Finance dashboard widget consumes the same data via the manager
 * REST endpoints (``/api/finance/invoices/``), so anything Miya logs
 * here shows up immediately in the manager's Finance lane.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { resolveAgentContext } from "../../services/agentContext";
import { noContextError } from "./_common/errors";

const _financeApi = new ApiService();


export class RecordInvoiceTool implements LuaTool {
    name = "record_invoice";
    description =
        "Log a new accounts-payable invoice (a bill the restaurant owes a vendor: supplier, utility, rent, taxes, contractor, maintenance, etc.). Use whenever the manager says 'log this invoice', 'we got a bill from X', 'record an invoice', or sends a photo of an invoice. Do NOT use this for staff payroll/payslips — those are PAYROLL staff_requests.";

    inputSchema = z.object({
        vendor: z.string().describe("Vendor / supplier name, as printed on the invoice."),
        amount: z.number().positive().describe("Total amount due, as a number (no currency symbol)."),
        due_date: z.string().describe("When the bill must be paid. Accepts YYYY-MM-DD, 'today', 'tomorrow', or any ISO date."),
        invoice_number: z.string().optional().describe("Invoice number printed on the bill. Strongly recommended for dedupe."),
        issue_date: z.string().optional().describe("Date the invoice was issued (YYYY-MM-DD)."),
        currency: z.string().optional().describe("3-letter currency code, e.g. USD, MAD, EUR. Defaults to the tenant's currency."),
        category: z.string().optional().describe("Free-text category like 'rent', 'electricity', 'maintenance', 'insurance'."),
        notes: z.string().optional().describe("Anything else worth remembering."),
        photo_url: z.string().optional().describe("URL of the invoice photo/PDF (e.g. WhatsApp media URL)."),
        location: z.string().optional().describe("Branch name or BusinessLocation id this bill belongs to. Optional."),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
    });

    constructor(private apiService: ApiService = _financeApi) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const ctx = await resolveAgentContext(input.restaurantId);
        const rid = ctx.restaurantId;
        if (!rid) return noContextError();

        const data = await this.apiService.recordInvoice(rid, {
            vendor: input.vendor,
            amount: input.amount,
            due_date: input.due_date,
            invoice_number: input.invoice_number,
            issue_date: input.issue_date,
            currency: input.currency,
            category: input.category,
            notes: input.notes,
            photo_url: input.photo_url,
            location: input.location,
        });
        if (data?.success === false) {
            return { status: "error", message: data?.message_for_user || data?.error || "Couldn't record invoice." };
        }
        const inv = data?.invoice;
        const photoHint = inv?.photo_url
            ? `\n\n::: documents\n[${input.vendor} Invoice](${inv.photo_url}) filename:invoice-${(input.invoice_number || inv.id || "new").replace(/\//g, "-")}.pdf mime:application/pdf\n:::`
            : "";

        return {
            status: "success",
            message: data?.message_for_user || "Invoice recorded.",
            created: data?.created !== false,
            invoice: inv,
            formatting_hint: photoHint || undefined,
            miya_directive:
                "Confirm the invoice creation to the user in their language. " +
                "If formatting_hint is present, include it VERBATIM so the invoice document renders as a card.",
        };
    }
}


export class MarkInvoicePaidTool implements LuaTool {
    name = "mark_invoice_paid";
    description =
        "Flip an existing accounts-payable invoice to PAID. Use when the manager says 'we paid X', 'mark the rent invoice paid', 'paid the electricity bill yesterday'. Identify the invoice by id (best) or by vendor + invoice_number (fallback for chat). Idempotent — if the invoice is already paid, returns its details without erroring.";

    inputSchema = z.object({
        invoice_id: z.string().optional().describe("Exact UUID of the invoice. Preferred."),
        vendor: z.string().optional().describe("Vendor name (used with invoice_number when id isn't known)."),
        invoice_number: z.string().optional().describe("Invoice number on the bill."),
        paid_on: z.string().optional().describe("Date the bill was paid (YYYY-MM-DD or ISO datetime). Defaults to now."),
        method: z
            .enum(["CASH", "CARD", "BANK_TRANSFER", "CHEQUE", "DIRECT_DEBIT", "OTHER"])
            .optional()
            .describe("How the bill was paid."),
        reference: z.string().optional().describe("Cheque number, transfer reference, or POS receipt id."),
        amount: z.number().optional().describe("If different from invoice amount (e.g. partial payment, fx adjustment)."),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
    });

    constructor(private apiService: ApiService = _financeApi) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const ctx = await resolveAgentContext(input.restaurantId);
        const rid = ctx.restaurantId;
        if (!rid) return noContextError();

        if (!input.invoice_id && !input.vendor && !input.invoice_number) {
            return {
                status: "error",
                message: "Tell me either the invoice id, or the vendor and invoice number.",
            };
        }

        const data = await this.apiService.markInvoicePaid(rid, {
            invoice_id: input.invoice_id,
            vendor: input.vendor,
            invoice_number: input.invoice_number,
            paid_on: input.paid_on,
            method: input.method,
            reference: input.reference,
            amount: input.amount,
        });
        if (data?.success === false) {
            return { status: "error", message: data?.message_for_user || data?.error || "Couldn't mark invoice paid." };
        }
        return {
            status: "success",
            message: data?.message_for_user || "Invoice marked paid.",
            already_paid: !!data?.already_paid,
            invoice: data?.invoice,
        };
    }
}


export class ListInvoicesTool implements LuaTool {
    name = "list_invoices";
    description =
        "List accounts-payable invoices for the tenant. Defaults to OPEN invoices (unpaid). Use when the manager asks 'what bills are due?', 'any overdue invoices?', 'how much do we owe X?', 'show me unpaid invoices', or 'invoices due this week'. Powers the Finance dashboard widget.";

    inputSchema = z.object({
        status: z
            .enum(["OPEN", "PAID", "VOIDED", "DRAFT", "ALL"])
            .optional()
            .default("OPEN")
            .describe("Which status bucket to show. Default OPEN (unpaid)."),
        vendor: z.string().optional().describe("Partial vendor name match."),
        overdue: z.boolean().optional().describe("Only show invoices past their due date (status=OPEN, due_date<today)."),
        due_within: z.number().int().positive().optional().describe("Only show OPEN invoices due in the next N days."),
        limit: z.number().int().positive().max(100).optional().default(25),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
    });

    constructor(private apiService: ApiService = _financeApi) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const ctx = await resolveAgentContext(input.restaurantId);
        const rid = ctx.restaurantId;
        if (!rid) return noContextError();

        const data = await this.apiService.listInvoices(rid, {
            status: input.status,
            vendor: input.vendor,
            overdue: input.overdue,
            due_within: input.due_within,
            limit: input.limit,
            user_id: ctx.userId,
            phone: ctx.phone,
            email:
                (user as any)?.data?.email ||
                (user as any)?.data?.emailAddress ||
                (user as any)?._luaProfile?.email ||
                (user as any)?._luaProfile?.emailAddress,
        });
        if (data?.success === false) {
            return {
                status: "error",
                message: data?.message_for_user || data?.error || "Couldn't list invoices.",
            };
        }

        const invoices = (data?.invoices || []).map((inv: any) => ({
            id: inv.id,
            vendor_name: inv.vendor_name,
            invoice_number: inv.invoice_number,
            amount: inv.amount,
            currency: inv.currency,
            due_date: inv.due_date,
            status: inv.status,
            is_overdue: inv.is_overdue,
            days_until_due: inv.days_until_due,
            category: inv.category,
        }));

        const listItems = invoices.slice(0, 10).map((inv: any) => {
            const overdueTag = inv.is_overdue ? " ⚠️ OVERDUE" : "";
            const dueInfo = inv.days_until_due != null
                ? (inv.days_until_due === 0 ? "Due today" : inv.days_until_due > 0 ? `Due in ${inv.days_until_due}d` : `${Math.abs(inv.days_until_due)}d overdue`)
                : `Due ${inv.due_date}`;
            return `::: list-item\n# ${inv.vendor_name}${overdueTag}\n${inv.currency || "USD"} ${Number(inv.amount).toFixed(2)} · ${dueInfo}. Invoice #${inv.invoice_number || "N/A"} · ${inv.category || "General"}\n:::`;
        });

        return {
            status: "success",
            message: data?.message_for_user || `${data?.count || 0} invoices.`,
            count: data?.count || 0,
            overdue_count: data?.overdue_count || 0,
            invoices,
            formatting_hint: listItems.length > 0
                ? listItems.join("\n\n") + "\n\n::: actions\n- Mark as Paid\n- Show Overdue Only\n- Add New Invoice\n:::"
                : undefined,
            miya_directive:
                "When displaying invoices, include the formatting_hint VERBATIM in your reply. " +
                "The ::: list-item blocks will render as visual cards. " +
                "Add a brief summary line before the cards (e.g. '3 unpaid invoices, 1 overdue').",
        };
    }
}
