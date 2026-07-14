/**
 * Sales report summary for a date or range using POS data.
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError } from "./_common/errors";
import {
    assertManagerToolAccess,
    readCachedRole,
} from "../../shared/roleGate";

function getRestaurantId(user: any) {
    const userData = user?.data || {};
    const profile = (user as any)?._luaProfile || {};
    return (user as any)?.restaurantId || userData.restaurantId || profile.restaurantId || (profile.metadata && (profile.metadata as any).restaurantId);
}

export default class SalesReportTool implements LuaTool {
    name = "sales_report";
    description = "Get sales summary and top items for a date or period. Use when the manager asks for 'sales report', 'how did we do yesterday', 'last week sales', or 'top selling items'. Uses POS data; if POS is disconnected the summary will indicate that.";

    inputSchema = z.object({
        date: z.string().optional().describe("Single date YYYY-MM-DD. If omitted, today."),
        days: z.number().optional().default(7).describe("For top_items: number of days to include (default 7)."),
        restaurantId: z.string().optional().describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]. Do NOT omit."),
    });

    constructor(private apiService: ApiService = new ApiService()) {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) return { status: "error", message: "No context." };
        const u = user as unknown as { data?: Record<string, unknown>; _luaProfile?: Record<string, unknown> };
        const gate = assertManagerToolAccess({
            toolName: "sales_report",
            role: readCachedRole(u),
            cachedAudience: u.data?.mizanAudience,
        });
        if (!gate.ok) {
            return { status: "error", message: gate.message, miya_directive: "Relay message_for_user verbatim." };
        }
        const rid = input.restaurantId || getRestaurantId(user);
        if (!rid) return noContextError();

        const date = input.date || new Date().toISOString().slice(0, 10);
        const [summary, topItems] = await Promise.all([
            this.apiService.getPosSalesSummary(rid, date),
            this.apiService.getPosTopItems(rid, input.days || 7, 10),
        ]);

        const statusOk = summary?.success !== false && (summary?.connected !== false);
        let message = `Sales for ${date}: `;
        if (summary?.total_sales != null) {
            message += `Total ${summary.currency || "USD"} ${Number(summary.total_sales).toFixed(2)}`;
            if (summary.order_count != null) message += ` (${summary.order_count} orders)`;
            if (summary.tips != null) message += `, Tips ${Number(summary.tips).toFixed(2)}`;
            message += ". ";
        } else if (summary?.connected === false || summary?.error) {
            message += "POS disconnected or unavailable. ";
        } else {
            message += "No sales data for this date. ";
        }
        if (topItems?.items?.length) {
            message += `Top items (last ${input.days || 7} days): ${topItems.items.slice(0, 5).map((i: any) => `${i.name || i.item_name} (${i.quantity || i.count})`).join(", ")}.`;
        }
        const topItemsList = (topItems?.items || []).slice(0, 10);
        const listItems = topItemsList.map((item: any) => {
            const name = item.name || item.item_name || "Unknown";
            const qty = item.quantity || item.count || 0;
            const revenue = item.total_revenue || item.revenue;
            const revenueStr = revenue != null ? ` · ${summary?.currency || "USD"} ${Number(revenue).toFixed(2)}` : "";
            return `::: list-item\n# ${name}\n${qty} sold${revenueStr}. Top seller in the last ${input.days || 7} days.\n:::`;
        });

        return {
            status: "success",
            message,
            summary: summary?.total_sales != null ? { total_sales: summary.total_sales, order_count: summary.order_count, tips: summary.tips, currency: summary.currency } : undefined,
            top_items: topItemsList,
            formatting_hint: listItems.length > 0
                ? listItems.join("\n\n") + "\n\n::: actions\n- View Full Report\n- Compare to Last Week\n- Show by Category\n:::"
                : undefined,
            miya_directive:
                "Present the sales summary first as text, then include the formatting_hint VERBATIM " +
                "for the top items — they'll render as visual cards. Add action buttons for next steps.",
        };
    }
}
