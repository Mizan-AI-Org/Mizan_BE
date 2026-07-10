/**
 * Execute operational intents via ApiService (used by specialist-agent preprocessors).
 */
import ApiService from "../services/ApiService";
import { resolveAgentContext } from "../services/agentContext";
import type { OperationCommandIntent } from "./operationIntent";

export type OperationsTenantContext = {
    restaurantId: string;
    userId?: string;
    email?: string;
    phone?: string;
};

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

export async function ensureDashboardWidget(
    api: ApiService,
    widgetId: string,
    restaurantId: string,
    ctx: { userId?: string; email?: string; phone?: string },
): Promise<void> {
    return ensureWidget(api, widgetId, restaurantId, ctx);
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

function shortRef(id: string | undefined): string {
    if (!id) return "";
    const clean = id.replace(/-/g, "");
    return clean.slice(-8).toLowerCase();
}

export async function executeOperationsIntent(
    intent: OperationCommandIntent,
    api: ApiService = new ApiService(),
    tenantOverride?: OperationsTenantContext,
): Promise<OperationsExecuteResult> {
    const resolved = tenantOverride
        ? {
              restaurantId: tenantOverride.restaurantId,
              userId: tenantOverride.userId,
              email: tenantOverride.email,
              phone: tenantOverride.phone,
              token: undefined,
              agentKey: undefined,
          }
        : await resolveAgentContext();
    const restaurantId = resolved.restaurantId;
    const ctx = resolved;
    if (!restaurantId) {
        return { status: "error", message: "I couldn't link this to your workspace yet." };
    }

    // Loose alias — specialist agents ship different ApiService versions; some
    // methods/fields don't exist on older builds. Optional methods are guarded
    // by presence checks below, and extra request fields are ignored server-side.
    const A = api as any;

    switch (intent.kind) {
        case "lookup": {
            const searchFn = A.searchOperationalRecordsForAgent;
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
            const chaseFn = A.chaseOperationalRecordForAgent;
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
            const result = await A.createDashboardTaskForAgent(restaurantId, {
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
            const result = await A.createStaffRequestForAgent({
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
            const result = await A.createStaffRequestForAgent({
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
            const result = await A.recordInvoice(restaurantId, {
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

        case "mark_invoice_paid": {
            const markFn = A.markInvoicePaid;
            if (!markFn) {
                return { status: "error", message: "Marking invoices paid isn't available on this channel." };
            }
            const data = await markFn.call(api, restaurantId, {
                vendor: intent.vendor,
                invoice_number: intent.invoiceNumber,
                paid_on: intent.paidOn,
                method: intent.paymentMethod,
                amount: intent.amount,
                reference: intent.reference,
            });
            if (data?.success === false) {
                // Fallback: record the bill as unpaid tracking if mark-paid can't find it
                if (intent.amount != null && intent.vendor) {
                    const recorded = await A.recordInvoice(restaurantId, {
                        vendor: intent.vendor,
                        amount: intent.amount,
                        due_date: intent.paidOn,
                        invoice_number: intent.invoiceNumber,
                        notes: [
                            intent.paymentMethod ? `Payment method: ${intent.paymentMethod}` : "",
                            `Paid on: ${intent.paidOn}`,
                        ]
                            .filter(Boolean)
                            .join(". "),
                        currency: "MAD",
                    });
                    if (recorded?.success !== false) {
                        await ensureWidget(api, "finance", restaurantId, ctx);
                        return {
                            status: "success",
                            message:
                                recorded.message_for_user ||
                                `✓ Payment noted for ${intent.vendor}${intent.invoiceNumber ? ` (facture ${intent.invoiceNumber})` : ""} on ${intent.paidOn}.`,
                            record_id: recorded.invoice?.id,
                        };
                    }
                }
                return {
                    status: "error",
                    message: data.message_for_user || data.error || "Couldn't mark the invoice as paid.",
                };
            }
            await ensureWidget(api, "finance", restaurantId, ctx);
            return {
                status: "success",
                message:
                    data.message_for_user ||
                    `✓ Payment recorded${intent.invoiceNumber ? ` for facture ${intent.invoiceNumber}` : ""} on ${intent.paidOn}.`,
                record_id: data.invoice?.id || data.invoice_id,
            };
        }

        case "attendance_report": {
            const report = await A.getAttendanceReport(restaurantId, intent.date);
            const rows = report?.summary || [];
            if (!rows.length) {
                return {
                    status: "success",
                    message: intent.date
                        ? `No attendance records found for ${intent.date}.`
                        : "No attendance records found for today.",
                };
            }
            const late = rows.filter((r) => r.status === "LATE" || r.status === "ABSENT");
            const onTime = rows.filter((r) => r.status === "ON_TIME").length;
            const lines = rows.slice(0, 12).map((r) => {
                const label =
                    r.status === "ON_TIME"
                        ? `on time${r.clock_in ? ` (${r.clock_in})` : ""}`
                        : r.status === "LATE"
                          ? `late${r.lateness_minutes ? ` +${r.lateness_minutes}m` : ""}`
                          : r.status.toLowerCase();
                return `• ${r.staff_name} — ${label}`;
            });
            const header = intent.date ? `Attendance for ${intent.date}` : "Attendance";
            return {
                status: "success",
                message: `${header}: ${onTime} on time, ${late.length} late/absent.\n${lines.join("\n")}`,
            };
        }

        case "self_role": {
            if (!ctx.phone) {
                return {
                    status: "error",
                    message: "I couldn't identify your profile from this channel. Ask HR if you need your role confirmed.",
                };
            }
            const looked = await A.getStaffByPhoneForAgent(ctx.phone);
            if (!looked.success || !looked.found || !looked.staff) {
                return {
                    status: "success",
                    message:
                        "I couldn't find your staff profile linked to this number. Check with HR if your WhatsApp isn't registered.",
                };
            }
            const staff = looked.staff;
            const role = staff.role || staff.position || "not set";
            const name =
                staff.full_name ||
                `${staff.first_name || ""} ${staff.last_name || ""}`.trim() ||
                "you";
            return {
                status: "success",
                message: `${name} — your role is ${role}.`,
            };
        }

        case "generate_payslip": {
            const genFn = A.generatePayslipForAgent;
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
            const logFn = A.logTemperatureForAgent;
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
            const payFn = A.updateInvoiceBankPaymentStatusForAgent;
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
            const syncFn = A.syncDeliveryMenuForAgent;
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
            const seedFn = A.seedComplianceRemindersForAgent;
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

        case "payroll_escalation": {
            const hr = await A.createStaffRequestForAgent({
                restaurant_id: restaurantId,
                subject: `Payroll follow-up: ${intent.staffName}`,
                description: intent.description,
                category: "PAYROLL",
                priority: "HIGH",
                follow_up_enabled: true,
            });
            const fin = await A.createStaffRequestForAgent({
                restaurant_id: restaurantId,
                subject: `Salary payment: ${intent.staffName}`,
                description: intent.description,
                category: "FINANCE",
                priority: "HIGH",
                follow_up_enabled: true,
            });
            if (hr.success === false && fin.success === false) {
                return {
                    status: "error",
                    message: hr.error || fin.error || "Couldn't notify HR and Finance.",
                };
            }
            await ensureWidget(api, "human_resources", restaurantId, ctx);
            await ensureWidget(api, "finance", restaurantId, ctx);
            const refs = [shortRef(hr.id), shortRef(fin.id)].filter(Boolean);
            const refText = refs.length ? ` Reference: ${refs.join(", ")}.` : "";
            return {
                status: "success",
                message: `✓ Logged with HR and Finance for ${intent.staffName}.${refText}`,
                record_id: hr.id || fin.id,
            };
        }

        case "ops_schedule_note":
        case "event_prep_reminder": {
            const descParts = [intent.description];
            if (intent.kind === "event_prep_reminder" && intent.eventName) {
                descParts.unshift(`Event: ${intent.eventName}`);
            }
            const result = await A.createDashboardTaskForAgent(restaurantId, {
                title: intent.title,
                description: descParts.join("\n"),
                category: intent.kind === "event_prep_reminder" ? "MEETING" : "OPERATIONS",
                assign_to_self: true,
                notify_whatsapp: false,
                follow_up_enabled: false,
                due_date: intent.kind === "ops_schedule_note" ? intent.dueDate : undefined,
                sender_phone: ctx.phone,
            });
            if (!result.success) {
                return {
                    status: "error",
                    message: result.message_for_user || result.error || "Couldn't save the note.",
                };
            }
            const widgetId = intent.kind === "event_prep_reminder" ? "meetings" : "operations_tasks";
            await ensureWidget(api, widgetId, restaurantId, ctx);
            return {
                status: "success",
                message: result.message_for_user || `✓ Saved: "${intent.title}"`,
                task_ref: result.task_ref,
                record_id: result.record_id || result.task?.id,
            };
        }

        case "calendar_appointment": {
            const data = await A.createCalendarEvent(restaurantId, {
                title: intent.title,
                start: intent.start,
                end: intent.end,
                location: intent.location,
                is_reminder: false,
            });

            if (data?.success === false && data?.error === "calendar_not_connected") {
                const fallback = await A.createDashboardTaskForAgent(restaurantId, {
                    title: intent.title,
                    description: [
                        `Start: ${intent.start}`,
                        intent.end ? `End: ${intent.end}` : "",
                        intent.location ? `Location: ${intent.location}` : "",
                    ]
                        .filter(Boolean)
                        .join("\n"),
                    category: "MEETING",
                    assign_to_self: true,
                    due_date: intent.start.slice(0, 10),
                    sender_phone: ctx.phone,
                });
                if (!fallback.success) {
                    return {
                        status: "error",
                        message:
                            data.message_for_user ||
                            fallback.message_for_user ||
                            "Couldn't add the appointment.",
                    };
                }
                await ensureWidget(api, "meetings", restaurantId, ctx);
                return {
                    status: "success",
                    message:
                        fallback.message_for_user ||
                        `✓ Added "${intent.title}" to your agenda (${intent.start.slice(0, 16).replace("T", " ")}).`,
                    task_ref: fallback.task_ref,
                    record_id: fallback.record_id || fallback.task?.id,
                };
            }

            if (data?.success === false) {
                return {
                    status: "error",
                    message: data.message_for_user || data.error || "Couldn't add the appointment.",
                };
            }
            await ensureWidget(api, "meetings", restaurantId, ctx);
            return {
                status: "success",
                message: data.message_for_user || `✓ Added "${intent.title}" to your calendar.`,
                record_id: data.event_id,
            };
        }

        default:
            return { status: "error", message: "Unknown intent." };
    }
}

export function toolMessage(result: OperationsExecuteResult | Record<string, unknown>): string {
    return msg(result);
}
