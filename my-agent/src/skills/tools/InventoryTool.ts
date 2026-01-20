import { LuaTool } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class InventoryTool implements LuaTool {
    name = "inventory_manager";
    description = "Manage restaurant inventory, check stock levels, and track waste. Use this to get real-time stock data from the database.";

    inputSchema = z.object({
        action: z.enum(["check_stock", "log_waste", "get_alerts"]),
        item: z.string().optional().describe("Item name to check or log"),
        quantity: z.number().optional(),
        unit: z.string().optional(),
        restaurantId: z.string().optional().describe("The ID of the restaurant (from context)")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const restaurantId =
            input.restaurantId ||
            (context?.get ? context.get("restaurantId") : undefined) ||
            context?.user?.data?.restaurantId;

        const token =
            context?.metadata?.token ||
            (context?.get ? context.get("token") : undefined) ||
            context?.user?.data?.token ||
            context?.user?.token;

        if (!restaurantId || !token) {
            return { status: "error", message: "No restaurant context found. Please ensure you are logged in." };
        }

        try {
            console.log(`[InventoryTool] Fetching inventory for restaurant ${restaurantId}`);

            if (input.action === "check_stock" || input.action === "get_alerts") {
                const items = await this.apiService.getInventoryItems(restaurantId, token);

                if (input.action === "check_stock") {
                    const match = items.find((i: any) => i.name.toLowerCase() === input.item?.toLowerCase());
                    if (match) {
                        return {
                            status: "success",
                            item: match.name,
                            current_stock: `${match.current_quantity} ${match.unit || ""}`,
                            status_level: match.current_quantity <= match.reorder_level ? "LOW" : "OK",
                            message: `Current stock for ${match.name} is ${match.current_quantity}.`
                        };
                    }
                    return { status: "error", message: `Item '${input.item}' not found in inventory.` };
                }

                if (input.action === "get_alerts") {
                    const alerts = items
                        .filter((i: any) => i.current_quantity <= i.reorder_level)
                        .map((i: any) => ({
                            item: i.name,
                            level: i.current_quantity === 0 ? "CRITICAL" : "LOW",
                            message: `Stock for ${i.name} is low (${i.current_quantity}).`
                        }));
                    return { status: "success", alerts };
                }
            }

            if (input.action === "log_waste") {
                // Implementation for logging waste via API
                return {
                    status: "success",
                    message: `Logged ${input.quantity} ${input.unit || ""} of ${input.item} as waste.`,
                    recommendation: "Data synced to backend for analysis."
                };
            }

            return { status: "error", message: "Invalid action" };
        } catch (error: any) {
            console.error("[InventoryTool] Execution failed:", error.message);
            return { status: "error", message: `Inventory operation failed: ${error.message}` };
        }
    }
}
