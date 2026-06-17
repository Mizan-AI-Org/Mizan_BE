/**
 * Execute operational intents via ApiService (used by specialist-agent preprocessors).
 */
import ApiService from "../services/ApiService";
import { resolveAgentContext } from "../services/agentContext";
import type { OperationCommandIntent } from "./operationIntent";

export type OperationsExecuteResult = {
    status: string;
    message?: string;
    message_for_user?: string;
    task_ref?: string;
    record_id?: string;
    recordId?: string;
};

async function ensureWidget(
    api: ApiService,
    widgetId: string,
    restaurantId: string,
    ctx: { userId?: string; email?: string; phone?: string },
): Promise<void> {
    try {
        await api.manageDashboardWidgetsForAgent(restaurantId, "add", {
            widgets: [widgetId],
            user_id: ctx.userId,
            email: ctx.email,
            phone: ctx.phone,
        });
    } catch {
        /* best-effort */
    }
}

function msg(r: OperationsExecuteResult | Record<string, unknown>): string {
    return String(r.message || r.message_for_user || "").trim();
}

function staffRequestMessage(result: Record<string, unknown>, fallback: string): string {
    const userMsg = String(result.message_for_user || "").trim();
    if (userMsg) return userMsg;
    const base = fallback;
    const waSent = Boolean(result.whatsapp_sent);
    const assignee = (result as { assignee?: { name?: string } }).assignee;
    if (assignee?.name) {
        return waSent
            ? `${base} — ${assignee.name} has been notified on WhatsApp. I'll follow up automatically if they don't respond.`
            : `${base} — ${assignee.name} will see it in their inbox.`;
    }
    return base;
}

export async function executeOperationsIntent(
    intent: OperationCommandIntent,
    api: ApiService = new ApiService(),
): Promise<OperationsExecuteResult> {
    const ctx = await resolveAgentContext();
    const restaurantId = ctx.restaurantId;
    if (!restaurantId) {
        return { status: "error", message: "I couldn't link this to your workspace yet." };
    }

    switch (intent.kind) {
        case "lookup": {
            const searchFn = (api as { searchOperationalRecordsForAgent?: typeof api.searchOperationalRecordsForAgent })
                .searchOperationalRecordsForAgent;
            if (!searchFn) {
                return { status: "error", message: "Record search isn't available on this channel." };
            }
            const data = await searchFn.call(api, restaurantId, intent.query);
            if (data?.success === false) {
                return { status: "error", message: data.message_for_user || data.error || "Search failed." };
            }
            const matches = data.matches || [];
            if (!matches.length) {
                return {
                    status: "success",
                    message:
                        "I couldn't find anything matching that reference. Tell me what to log and I'll create it now.",
                };
            }
            const lines = matches.slice(0, 5).map((m: any) => {
                const ref = m.ref || m.id?.slice(-8)?.toUpperCase();
                return `• ${m.type}: "${m.title || m.subject}" (#${ref}) — ${m.dashboard_hint || m.lane || ""}`;
            });
            return {
                status: "success",
                message: `Found ${matches.length} match(es):\n${lines.join("\n")}`,
            };
        }

        case "chase": {
            const chaseFn = (api as { chaseOperationalRecordForAgent?: typeof api.chaseOperationalRecordForAgent })
                .chaseOperationalRecordForAgent;
            if (!chaseFn) {
                return { status: "error", message: "Follow-up isn't available on this channel." };
            }
            const data = await chaseFn.call(api, restaurantId, intent.query);
            if (data?.success === false) {
                return {
                    status: "error",
                    message: data.message_for_user || data.error || "Couldn't send the follow-up.",
                };
            }
            return {
                status: "success",
                message: data.message_for_user || "Follow-up sent.",
                record_id: data.record_id,
            };
        }

        case "personal_ops_reminder":
        case "dashboard_reminder": {
            const category = intent.kind === "dashboard_reminder" ? intent.category : "OPERATIONS";
            const widgetId =
                intent.kind === "dashboard_reminder"
                    ? intent.widgetId || "human_resources"
                    : "operations_tasks";
            const result = await api.createDashboardTaskForAgent(restaurantId, {
                title: intent.title,
                description: intent.description,
                category,
                assign_to_self: true,
                notify_whatsapp: false,
                follow_up_enabled: false,
                sender_phone: ctx.phone,
            });
            if (!result.success) {
                return {
                    status: "error",
                    message: result.message_for_user || result.error || "Couldn't save the reminder.",
                };
            }
            await ensureWidget(api, widgetId, restaurantId, ctx);
            return {
                status: "success",
                message: result.message_for_user,
                task_ref: result.task_ref,
                record_id: result.record_id || result.task?.id,
            };
        }

        case "purchase_order": {
            const result = await api.createStaffRequestForAgent({
                restaurant_id: restaurantId,
                subject: intent.subject,
                description: intent.description,
                category: "PURCHASE_ORDER",
                priority: intent.priority || "MEDIUM",
                follow_up_enabled: true,
            });
            if (result.success === false) {
                return { status: "error", message: result.error || "Couldn't log the purchase request." };
            }
            await ensureWidget(api, "purchase_orders", restaurantId, ctx);
            return {
                status: "success",
                message: staffRequestMessage(result as Record<string, unknown>, `✓ Logged purchase order: "${intent.subject}"`),
                record_id: result.id,
            };
        }

        case "maintenance": {
            const result = await api.createStaffRequestForAgent({
                restaurant_id: restaurantId,
                subject: intent.subject,
                description: intent.description,
                category: "MAINTENANCE",
                priority: intent.priority || "MEDIUM",
                follow_up_enabled: true,
            });
            if (result.success === false) {
                return { status: "error", message: result.error || "Couldn't log the maintenance request." };
            }
            await ensureWidget(api, "maintenance", restaurantId, ctx);
            return {
                status: "success",
                message: staffRequestMessage(
                    result as Record<string, unknown>,
                    `✓ Logged maintenance request: "${intent.subject}"`,
                ),
                record_id: result.id,
            };
        }

        case "record_invoice": {
            const result = await api.recordInvoice(restaurantId, {
                vendor: intent.vendor,
                amount: intent.amount,
                due_date: intent.dueDate,
                invoice_number: intent.invoiceNumber,
                notes: intent.notes,
                currency: intent.currency,
            });
            if (result?.success === false) {
                return {
                    status: "error",
                    message: result.message_for_user || result.error || "Couldn't record the invoice.",
                };
            }
            await ensureWidget(api, "finance", restaurantId, ctx);
            return {
                status: "success",
                message: result.message_for_user || "Invoice recorded.",
                record_id: result.invoice?.id,
            };
        }

        case "generate_payslip": {
            const genFn = (api as { generatePayslipForAgent?: typeof api.generatePayslipForAgent })
                .generatePayslipForAgent;
            if (!genFn) {
                return { status: "error", message: "Payslip generation isn't available on this channel." };
            }
            const data = await genFn.call(api, restaurantId, {
                staff_name: intent.staffName,
                month: intent.month,
                year: intent.year,
                period_start: intent.periodStart,
                period_end: intent.periodEnd,
            });
            if (data?.success === false) {
                return {
                    status: "error",
                    message: data.message_for_user || data.error || "Couldn't generate payslips.",
                };
            }
            await ensureWidget(api, "human_resources", restaurantId, ctx);
            return {
                status: "success",
                message: data.message_for_user || `Generated ${data.count ?? 1} payslip(s).`,
                record_id: data.payslips?.[0]?.payslip_id as string | undefined,
            };
        }

        case "temperature_log": {
            const logFn = (api as { logTemperatureForAgent?: typeof api.logTemperatureForAgent })
                .logTemperatureForAgent;
            if (!logFn) {
                return { status: "error", message: "Temperature logging isn't available on this channel." };
            }
            const data = await logFn.call(api, restaurantId, {
                equipment: intent.equipment,
                value_c: intent.valueC,
                text: intent.text,
            });
            if (data?.success === false) {
                return {
                    status: "error",
                    message: data.message_for_user || data.error || "Couldn't log the temperature.",
                };
            }
            await ensureWidget(api, "maintenance", restaurantId, ctx);
            return {
                status: "success",
                message: data.message_for_user || "Temperature logged.",
                record_id: data.record_id,
            };
        }

        case "bank_payment_status": {
            const payFn = (api as {
                updateInvoiceBankPaymentStatusForAgent?: typeof api.updateInvoiceBankPaymentStatusForAgent;
            }).updateInvoiceBankPaymentStatusForAgent;
            if (!payFn) {
                return { status: "error", message: "Bank payment status isn't available on this channel." };
            }
            const data = await payFn.call(api, restaurantId, {
                vendor: intent.vendor,
                invoice_number: intent.invoiceNumber,
                bank_payment_status: intent.status,
                reference: intent.reference,
                bank_payment_note: intent.note,
            });
            if (data?.success === false) {
                return {
                    status: "error",
                    message: data.message_for_user || data.error || "Couldn't update payment status.",
                };
            }
            await ensureWidget(api, "finance", restaurantId, ctx);
            return {
                status: "success",
                message: data.message_for_user || "Payment status updated.",
                record_id: (data.invoice as { id?: string } | undefined)?.id,
            };
        }

        case "delivery_menu_sync": {
            const syncFn = (api as { syncDeliveryMenuForAgent?: typeof api.syncDeliveryMenuForAgent })
                .syncDeliveryMenuForAgent;
            if (!syncFn) {
                return { status: "error", message: "Delivery menu sync isn't available on this channel." };
            }
            const data = await syncFn.call(api, restaurantId, { provider: intent.provider });
            if (data?.success === false) {
                return {
                    status: "error",
                    message: data.message_for_user || data.error || "Couldn't sync the delivery menu.",
                };
            }
            return {
                status: "success",
                message: data.message_for_user || "Delivery menu synced.",
            };
        }

        case "seed_compliance": {
            const seedFn = (api as { seedComplianceRemindersForAgent?: typeof api.seedComplianceRemindersForAgent })
                .seedComplianceRemindersForAgent;
            if (!seedFn) {
                return { status: "error", message: "Compliance calendar isn't available on this channel." };
            }
            const data = await seedFn.call(api, restaurantId);
            if (data?.success === false) {
                return {
                    status: "error",
                    message: data.message_for_user || data.error || "Couldn't seed compliance reminders.",
                };
            }
            await ensureWidget(api, "finance", restaurantId, ctx);
            return {
                status: "success",
                message: data.message_for_user || "Compliance reminders added.",
            };
        }

        default:
            return { status: "error", message: "Unknown intent." };
    }
}

export function toolMessage(result: OperationsExecuteResult | Record<string, unknown>): string {
    return msg(result);
}
