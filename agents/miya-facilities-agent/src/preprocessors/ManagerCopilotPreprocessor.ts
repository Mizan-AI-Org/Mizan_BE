/**
 * Deterministic manager copilot slice: today's sales, low stock, purchase recommendations.
 * Blocks LLM invent of KPI numbers.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import { extractLastUserText } from "../utils/extractLastUserText";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";
import {
    resolveStaffPhoneForByPhoneTools,
    type LuaUserPhoneSource,
} from "../utils/resolveStaffPhoneFromLuaUser";
import { classifyManagerCopilotAsk } from "../shared/managerCopilotIntent";
import {
    assertManagerToolAccess,
    readCachedRole,
    resolveAudience,
} from "../shared/roleGate";

function phoneFromUser(user: UserDataInstance): string {
    const u = user as unknown as LuaUserPhoneSource & { uid?: string };
    return resolveStaffPhoneForByPhoneTools(
        {
            uid: u.uid,
            data: (u as { data?: Record<string, unknown> }).data,
            _luaProfile: (u as { _luaProfile?: Record<string, unknown> })._luaProfile,
        },
        null,
    );
}

async function ensureRole(
    user: UserDataInstance,
    channel: string,
    api: ApiService,
): Promise<string> {
    const u = user as unknown as {
        data?: Record<string, unknown>;
        _luaProfile?: Record<string, unknown>;
    };
    let role = readCachedRole(u);
    if (role) return role;

    const phone = phoneFromUser(user);
    if (phone) {
        try {
            const lookup = await api.getStaffByPhoneForAgent(phone);
            if (lookup.success && lookup.staff?.role) {
                role = String(lookup.staff.role);
                u.data = { ...(u.data || {}), role, mizanAudience: resolveAudience({ role, channel }) };
            }
        } catch {
            /* best-effort */
        }
    }
    return role;
}

function todayIso(): string {
    return new Date().toISOString().slice(0, 10);
}

export const managerCopilotPreprocessor = new PreProcessor({
    name: "manager-copilot-router",
    description: "Answers manager sales / low-stock / purchase-recommend asks via live APIs.",
    priority: 188,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        const lastText = extractLastUserText(messages);
        const kind = classifyManagerCopilotAsk(lastText);
        if (!kind) return { action: "proceed" as const };

        const api = new ApiService();
        const role = await ensureRole(user, channel, api);
        const u = user as unknown as { data?: Record<string, unknown> };
        const audience = resolveAudience({
            role,
            channel,
            cachedAudience: u.data?.mizanAudience,
        });

        // Low stock is readable by staff; sales / purchases / food-cost are manager-only.
        if (kind === "sales_today" || kind === "recommend_purchases" || kind === "food_cost") {
            const g = assertManagerToolAccess({
                toolName:
                    kind === "sales_today"
                        ? "sales_report"
                        : kind === "food_cost"
                          ? "food_cost"
                          : "supplier_order",
                role,
                channel,
                cachedAudience: audience,
            });
            if (!g.ok) {
                return { action: "block" as const, response: g.message };
            }
        }

        const tenant = await resolveTenantForUser(user);
        const rid = tenant.restaurantId;
        if (!rid) {
            return {
                action: "block" as const,
                response:
                    "I couldn't link this chat to a restaurant yet. Open Miya from the Mizan app, or message from your registered WhatsApp number.",
            };
        }

        try {
            if (kind === "food_cost") {
                const report = await api.getFoodCostForAgent(rid, 12);
                if (!report?.success && report?.error) {
                    return {
                        action: "block" as const,
                        response:
                            "I couldn't load recipe food-cost just now. Please try again — I won't invent margins.",
                    };
                }
                const items = report?.items || [];
                if (!items.length) {
                    return {
                        action: "block" as const,
                        response:
                            report?.message_for_user ||
                            "No recipes with costed ingredients yet. Add recipes + ingredient costs, then ask again.",
                        metadata: { manager_copilot: "food_cost", count: 0 },
                    };
                }
                const lines = items.slice(0, 10).map(
                    (i: {
                        name?: string;
                        food_cost_pct?: number;
                        portion_cost?: number;
                        price?: number;
                        margin?: number;
                    }) =>
                        `• *${i.name}* — food cost ${i.food_cost_pct ?? "?"}%` +
                        ` (portion ${i.portion_cost ?? "?"}, sell ${i.price ?? "?"}, margin ${i.margin ?? "?"})`,
                );
                const avg =
                    report?.avg_food_cost_pct != null
                        ? `\nAverage food cost: *${report.avg_food_cost_pct}%* across ${report.total_with_recipes} recipes.`
                        : "";
                return {
                    action: "block" as const,
                    response:
                        `*Food cost / margin* (highest food-cost % first):\n${lines.join("\n")}${avg}` +
                        `\n\nWant me to recommend purchases for low stock next?`,
                    metadata: { manager_copilot: "food_cost", count: items.length },
                };
            }

            if (kind === "sales_today") {
                const date = todayIso();
                const summary = await api.getPosSalesSummary(rid, date);
                const top = await api.getPosTopItems(rid, 7, 5);
                if (summary?.connected === false || summary?.error) {
                    return {
                        action: "block" as const,
                        response:
                            `I couldn't load live sales for *${date}* — POS looks disconnected or unavailable. ` +
                            `Connect POS in settings, or ask again once sales are syncing.`,
                    };
                }
                const currency = summary?.currency || "MAD";
                const total =
                    summary?.total_sales != null
                        ? `${currency} ${Number(summary.total_sales).toFixed(2)}`
                        : "no total yet";
                const orders =
                    summary?.order_count != null ? ` · ${summary.order_count} orders` : "";
                let reply = `*Today's sales* (${date}): ${total}${orders}.`;
                const items = top?.items || [];
                if (items.length) {
                    reply +=
                        "\n\nTop items (7d): " +
                        items
                            .slice(0, 5)
                            .map(
                                (i: { name?: string; item_name?: string; quantity?: number; count?: number }) =>
                                    `${i.name || i.item_name} (${i.quantity ?? i.count ?? 0})`,
                            )
                            .join(", ") +
                        ".";
                }
                reply += "\n\nWant a compare to yesterday or last week?";
                return {
                    action: "block" as const,
                    response: reply,
                    metadata: { manager_copilot: "sales_today" },
                };
            }

            const inv = await api.getInventoryItemsForAgent(rid);
            const items = inv.items || [];
            const lowStock = items.filter(
                (i: { reorder_level?: number; current_stock?: number }) =>
                    i.reorder_level != null &&
                    Number(i.current_stock) <= Number(i.reorder_level),
            );

            if (kind === "low_stock") {
                if (!lowStock.length) {
                    return {
                        action: "block" as const,
                        response: `Inventory looks healthy — none of ${items.length} item(s) are at or below reorder level right now.`,
                        metadata: { manager_copilot: "low_stock", count: 0 },
                    };
                }
                const lines = lowStock.slice(0, 12).map(
                    (i: { name?: string; current_stock?: number; unit?: string; reorder_level?: number }) =>
                        `• *${i.name}* — ${i.current_stock ?? "?"} ${i.unit || ""} (reorder ≤ ${i.reorder_level})`,
                );
                return {
                    action: "block" as const,
                    response:
                        `*Running low* (${lowStock.length}):\n${lines.join("\n")}` +
                        (lowStock.length > 12 ? `\n…and ${lowStock.length - 12} more.` : "") +
                        `\n\nSay *recommend today's purchases* and I'll suggest a reorder list.`,
                    metadata: { manager_copilot: "low_stock", count: lowStock.length },
                };
            }

            // recommend_purchases
            if (!lowStock.length) {
                return {
                    action: "block" as const,
                    response:
                        "Nothing is below reorder level right now — no purchase list to recommend from stock. " +
                        "You can still say *Order 6 bottles of…* to log a purchase order.",
                    metadata: { manager_copilot: "recommend_purchases", count: 0 },
                };
            }
            const lines = lowStock.slice(0, 10).map(
                (i: { name?: string; current_stock?: number; reorder_level?: number; unit?: string }) => {
                    const need = Math.max(
                        0,
                        Number(i.reorder_level) * 2 - Number(i.current_stock || 0),
                    );
                    return `• *${i.name}* — suggest +${need || i.reorder_level} ${i.unit || "units"} (now ${i.current_stock})`;
                },
            );
            return {
                action: "block" as const,
                response:
                    `*Purchase recommendations* from low stock (${lowStock.length}):\n${lines.join("\n")}\n\n` +
                    `Say *Order …* for a specific item to create a purchase request, or ask me to message a supplier.`,
                metadata: { manager_copilot: "recommend_purchases", count: lowStock.length },
            };
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[ManagerCopilotPreprocessor] failed:", em);
            return {
                action: "block" as const,
                response:
                    "I couldn't load that live figure just now. Please try again in a moment — I won't invent numbers.",
            };
        }
    },
});

export default managerCopilotPreprocessor;
