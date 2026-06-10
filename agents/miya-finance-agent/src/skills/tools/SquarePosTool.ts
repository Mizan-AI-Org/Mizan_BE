import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class SquarePosTool implements LuaTool {
    name = "square_pos";
    description = `Interact with the restaurant's POS system (Square, Custom API, Toast, Clover) to fetch sales data, analytics, recommendations, daily prep lists, and trigger data syncs.

Actions:
- sales_summary: Get sales for a specific date (revenue, orders, tips, payment breakdown)
- top_items: Best-selling menu items over a period
- sales_analysis: Trends, period comparisons, slow movers, and actionable recommendations
- prep_list: AI-generated daily prep list based on 4-week sales forecast + recipes + current inventory
- status: Check POS connection status
- sync_menu: Trigger menu sync from POS
- sync_orders: Pull & import orders from the external POS/API into Mizan (required for Custom API before first analysis)

Works with ALL POS providers: Square (OAuth), Custom API (URL + key), Toast, Clover.
For Custom API restaurants, run sync_orders first to import external data, then use sales_analysis/prep_list.

Trigger phrases: "sales report", "how did we do", "top sellers", "what should we prep", "prep list for tomorrow", "sync my orders", "pull sales data", "analyse des ventes", "liste de préparation", "شحال دارنا اليوم", "شنو نحضرو غدا", "أكثر طبق مباع", "sync orders"`;

    inputSchema = z.object({
        action: z.enum(["sales_summary", "top_items", "sales_analysis", "prep_list", "status", "sync_menu", "sync_orders"]).describe("The POS action to perform"),
        restaurantId: z.string().describe("The restaurant ID from your context"),
        date: z.string().optional().describe("Date (YYYY-MM-DD). For sales_summary: target day. For prep_list: target prep day (defaults to tomorrow)."),
        days: z.number().optional().describe("Period in days for top_items/sales_analysis (default 7)"),
        limit: z.number().optional().describe("Max items for top_items (default 10)")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>) {
        try {
            switch (input.action) {
                case "sales_summary": {
                    const summary = await this.apiService.getPosSalesSummary(input.restaurantId, input.date);
                    if (summary?.connected === false) {
                        return { status: "success", message: "Your POS is not connected yet. Go to Settings → POS Integration to connect Square, Toast, or Clover.", data: summary };
                    }
                    let msg = `Sales for ${summary.date || input.date || 'today'}: `;
                    msg += `${summary.currency || 'MAD'} ${Number(summary.total_sales || 0).toFixed(2)} total`;
                    if (summary.order_count) msg += ` (${summary.order_count} orders)`;
                    if (summary.avg_ticket) msg += `, avg ticket ${Number(summary.avg_ticket).toFixed(2)}`;
                    if (summary.tips) msg += `, tips ${Number(summary.tips).toFixed(2)}`;
                    if (summary.cash_total || summary.card_total) {
                        msg += `\nCash: ${Number(summary.cash_total || 0).toFixed(2)}, Card: ${Number(summary.card_total || 0).toFixed(2)}`;
                    }
                    return { status: "success", message: msg, data: summary };
                }
                case "top_items": {
                    const topItems = await this.apiService.getPosTopItems(input.restaurantId, input.days, input.limit);
                    let msg = `Top sellers (last ${input.days || 7} days):\n`;
                    if (topItems?.items?.length) {
                        msg += topItems.items.map((i: any, idx: number) =>
                            `${idx + 1}. ${i.name} — ${i.quantity} sold, ${Number(i.revenue || 0).toFixed(2)} revenue`
                        ).join('\n');
                    } else {
                        msg += "No sales data available for this period.";
                    }
                    return { status: "success", message: msg, data: topItems };
                }
                case "sales_analysis": {
                    const analysis = await this.apiService.getPosSalesAnalysis(input.restaurantId, input.days || 7);
                    if (!analysis?.success) {
                        return { status: "error", message: analysis?.error || "Could not generate analysis." };
                    }
                    let msg = `📊 Sales Analysis (last ${analysis.period_days} days):\n`;
                    msg += `Revenue: ${analysis.currency} ${Number(analysis.current?.total || 0).toFixed(2)}`;
                    if (analysis.revenue_change_pct !== 0) {
                        const arrow = analysis.revenue_change_pct > 0 ? '↑' : '↓';
                        msg += ` (${arrow} ${Math.abs(analysis.revenue_change_pct)}% vs previous period)`;
                    }
                    msg += `\nOrders: ${analysis.current?.count || 0}, Avg ticket: ${Number(analysis.current?.avg_ticket || 0).toFixed(2)}`;
                    if (analysis.top_items?.length) {
                        msg += `\n\nTop items: ${analysis.top_items.slice(0, 3).map((i: any) => `${i.name} (${i.quantity})`).join(', ')}`;
                    }
                    if (analysis.slow_items?.length) {
                        msg += `\nSlow movers: ${analysis.slow_items.slice(0, 3).map((i: any) => `${i.name} (${i.quantity})`).join(', ')}`;
                    }
                    if (analysis.recommendations?.length) {
                        msg += `\n\n💡 Recommendations:\n${analysis.recommendations.map((r: string) => `• ${r}`).join('\n')}`;
                    }
                    return { status: "success", message: msg, data: analysis };
                }
                case "prep_list": {
                    const prep = await this.apiService.getPosPrepList(input.restaurantId, input.date);
                    if (!prep?.success) {
                        return { status: "error", message: prep?.error || "Could not generate prep list." };
                    }
                    return { status: "success", message: prep.message_for_user || "Prep list generated.", data: prep };
                }
                case "status": {
                    const posStatus = await this.apiService.getPosStatus(input.restaurantId);
                    let msg = `POS Status: ${posStatus.provider || 'NONE'}`;
                    if (posStatus.is_connected) {
                        msg += ` ✅ Connected (Merchant: ${posStatus.merchant_id || 'N/A'})`;
                    } else {
                        msg += ` ❌ Not connected. Go to Settings → POS Integration to connect.`;
                    }
                    return { status: "success", message: msg, data: posStatus };
                }
                case "sync_menu": {
                    const sync = await this.apiService.syncPosMenu(input.restaurantId);
                    return { status: "success", message: sync.queued ? "Menu sync started. Items will be updated shortly." : "Menu sync completed.", data: sync };
                }
                case "sync_orders": {
                    const syncResult = await this.apiService.syncPosOrders(input.restaurantId);
                    if (!syncResult?.success) {
                        return { status: "error", message: syncResult?.error || "Order sync failed." };
                    }
                    const count = syncResult.orders_count || 0;
                    let msg = count > 0
                        ? `✅ Synced ${count} orders from your POS. You can now ask me for sales analysis or a prep list.`
                        : "Sync completed but no new orders were found. Your external API may not have recent data.";
                    return { status: "success", message: msg, data: syncResult };
                }
                default:
                    return { status: "error", message: `Unsupported action: ${input.action}` };
            }
        } catch (error: any) {
            console.error(`[SquarePosTool] Action ${input.action} failed:`, error.message);
            return { status: "error", message: error.message };
        }
    }
}
