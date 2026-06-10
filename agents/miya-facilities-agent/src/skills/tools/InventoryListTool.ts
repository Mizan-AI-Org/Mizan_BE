/**
 * List inventory items for the restaurant (agent-authenticated).
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError } from "./_common/errors";

function getRestaurantId(user: any) {
    const userData = user?.data || {};
    const profile = (user as any)?._luaProfile || {};
    return (user as any)?.restaurantId || userData.restaurantId || profile.restaurantId || (profile.metadata && (profile.metadata as any).restaurantId);
}

export default class InventoryListTool implements LuaTool {
    name = "list_inventory";
    description = "List inventory items for the restaurant (name, current stock, unit, reorder level, cost). Use when the manager asks about inventory, stock levels, or what needs reordering.";

    inputSchema = z.object({
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid) return noContextError();

        const result = await this.apiService.getInventoryItemsForAgent(rid);
        const items = result.items || [];
        const lowStock = items.filter((i: any) => i.reorder_level != null && Number(i.current_stock) <= Number(i.reorder_level));
        let message = `There are ${items.length} inventory item(s).`;
        if (lowStock.length) {
            message += ` ${lowStock.length} item(s) at or below reorder level: ${lowStock.map((i: any) => i.name).join(", ")}.`;
        }
        return { status: "success", items, count: items.length, low_stock: lowStock, message };
    }
}
