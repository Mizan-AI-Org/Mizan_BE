/**
 * PaymentApprovalTool (PayGuard) — amount-tiered invoice payment approvals.
 */
import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError, upstreamError } from "./_common/errors";

export default class PaymentApprovalTool implements LuaTool {
    name = "payment_approval";
    description =
        "PayGuard: hierarchical invoice payment approvals by currency + amount. " +
        "Each currency (MAD, EUR, USD, …) has its own amount ladder — match the invoice currency. " +
        "Use action='list' for pending approvals waiting on a rung. " +
        "action='start' to submit an invoice for approval. " +
        "action='approve' or 'reject' for the current rung. " +
        "action='get_policy' to explain the ladders per currency. " +
        "Triggers in any language: approve payment / approuver le paiement / موافقة على الدفع; " +
        "reject payment / refuser le paiement / رفض الدفع; " +
        "submit for approval / soumettre pour approbation / قدّم للموافقة. " +
        "When someone asks to pay a large bill, check PayGuard first — never invent approvals.";

    inputSchema = z.object({
        action: z.enum(["list", "start", "approve", "reject", "get_policy"]),
        invoice_id: z.string().optional(),
        vendor: z.string().optional(),
        invoice_number: z.string().optional(),
        note: z.string().optional(),
        restaurantId: z
            .string()
            .optional()
            .describe("ALWAYS pass Restaurant ID from persistent context."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    private resolveToken(user: any): string | undefined {
        return (
            env("LUA_WEBHOOK_API_KEY") ||
            env("WEBHOOK_API_KEY") ||
            env("MIZAN_SERVICE_TOKEN") ||
            user?.token ||
            user?.data?.token
        );
    }

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
        const token = this.resolveToken(user);

        const res = await this.apiService.paymentApprovalForAgent({
            restaurant_id: rid,
            token,
            action: input.action,
            invoice_id: input.invoice_id,
            vendor: input.vendor,
            invoice_number: input.invoice_number,
            note: input.note,
        });
        if (res && res.success === false) {
            return {
                status: "error",
                message: res.message_for_user || res.error || "PayGuard request failed.",
                ...upstreamError(res.error),
            };
        }
        return {
            status: "success",
            ...res,
            message: res?.message_for_user || "Done.",
            miya_directive:
                "Rewrite message_for_user in the user's current chat language (en/fr/ar/Darija). " +
                "Name the people waiting and the amount. " +
                "Never claim a payment is approved unless PayGuard status says so.",
        };
    }
}
