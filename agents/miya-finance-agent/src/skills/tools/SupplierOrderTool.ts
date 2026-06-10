/**
 * SupplierOrderTool — Manager orders from suppliers via WhatsApp.
 * "Order from Ahmed: 20kg chicken, 10kg rice"
 * Creates a PO and sends it to the supplier's WhatsApp.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class SupplierOrderTool implements LuaTool {
    name = "supplier_order";
    description =
        "Create a purchase order and send it to a supplier via WhatsApp. Use when a manager says " +
        "'order from [supplier]', 'commande chez [supplier]', 'send order to [supplier]', or similar. " +
        "Parse the items and quantities from the message.";

    inputSchema = z.object({
        supplier_name: z.string().describe("Supplier name (e.g. 'Ahmed', 'Marjane', 'Fournisseur Légumes')"),
        items: z.array(z.object({
            name: z.string().describe("Item name"),
            quantity: z.number().describe("Quantity to order"),
            unit: z.string().optional().describe("Unit (kg, liters, boxes, etc.)"),
        })).describe("List of items to order"),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    private apiService: ApiService;
    constructor(apiService?: ApiService) { this.apiService = apiService || new ApiService(); }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const user = await User.get();
        const restaurantId = input.restaurantId || (user as any)?.data?.restaurantId || context?.metadata?.restaurantId;
        if (!restaurantId) return { status: "error", message: "I couldn't determine which restaurant this is for." };

        const result = await this.apiService.sendSupplierOrder(restaurantId, {
            supplier_name: input.supplier_name,
            items: input.items,
        });

        return {
            status: result.success ? "sent" : "error",
            whatsapp_sent: result.whatsapp_sent,
            message: result.message_for_user || result.error,
        };
    }
}
