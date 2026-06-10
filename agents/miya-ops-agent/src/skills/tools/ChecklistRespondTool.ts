/**
 * ChecklistRespondTool
 *
 * Records a staff member's response (Yes/No/N/A) to the current checklist task
 * and returns the next task for Miya to send, or a completion summary.
 *
 * Miya drives the entire checklist conversation — Django is just the data API.
 */

import { LuaTool, User, env } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class ChecklistRespondTool implements LuaTool {
    name = "checklist_respond";
    description =
        "Record a staff member's response (Yes, No, or N/A) to their current checklist task " +
        "and get the next task. Call this EVERY TIME a staff member replies to a checklist task. " +
        "Phone is auto-resolved from WhatsApp context.";

    inputSchema = z.object({
        response: z.enum(["yes", "no", "n_a"]).describe(
            "The staff's response: 'yes' = task done, 'no' = not done, 'n_a' = not applicable"
        ),
        notes: z.string().optional().describe("Optional notes from the staff (e.g. reason for 'no')"),
        phone: z.string().optional().describe("Staff phone (auto-resolved from WhatsApp)")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    private resolvePhone(user: any, inputPhone?: string): string {
        if (!user) return inputPhone ? String(inputPhone).replace(/[^0-9]/g, "") : "";
        const userData = (user as any)?.data || {};
        const profile = (user as any)?._luaProfile || {};
        const uid = (user as any)?.uid;
        const phoneFromUid =
            uid && String(uid).includes(":") ? String(uid).split(":")[1] : uid;
        const phoneFromData = (userData as any).phone ?? (profile as any).phoneNumber ?? (profile as any).mobileNumber;
        const raw = [inputPhone, phoneFromData, phoneFromUid].find((p) => p && String(p).replace(/[^0-9]/g, "").length >= 6);
        return raw ? String(raw).replace(/[^0-9]/g, "") : "";
    }

    private resolveToken(user: any, context?: any): string | undefined {
        return (
            env("LUA_WEBHOOK_API_KEY") ||
            env("WEBHOOK_API_KEY") ||
            (user as any)?.token ||
            (user as any)?.data?.token ||
            context?.metadata?.token ||
            context?.user?.data?.token ||
            context?.user?.token
        );
    }

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        const user = await User.get();
        const token = this.resolveToken(user, context);
        const phone = this.resolvePhone(user || context?.user, input.phone);

        if (!token) {
            return { status: "error", message: "Connection issue. Please try again." };
        }
        if (!phone) {
            return { status: "error", message: "I couldn't identify your account. Please try again." };
        }

        try {
            console.log(`[ChecklistRespondTool] phone=${phone} response=${input.response}`);
            const result = await this.apiService.respondToChecklist(phone, input.response, token, input.notes);
            console.log(`[ChecklistRespondTool] Response:`, JSON.stringify(result));

            if (!result.success) {
                return {
                    status: "error",
                    message: result.message_for_user || result.error || "Could not record your response. Please try again.",
                };
            }

            const r = result as any;

            if (r.status === "completed") {
                const s = r.summary || {};
                return {
                    status: "completed",
                    message: r.message_for_user || `✅ Checklist complete! ${s.yes || 0} done, ${s.no || 0} not done, ${s.n_a || 0} skipped.`,
                    summary: r.summary,
                    instruction: "SEND this completion message to the staff. The checklist is finished.",
                };
            }

            if (r.status === "next_task" && r.current_task) {
                const t = r.current_task;
                return {
                    status: "next_task",
                    answered: r.answered,
                    total: r.total,
                    current_task: {
                        id: t.id,
                        index: t.index,
                        title: t.title,
                        description: t.description || "",
                    },
                    message: `✓ Recorded.\n\n📋 *Task ${t.index}/${r.total}:* ${t.title}` +
                        (t.description ? `\n${t.description}` : "") +
                        `\n\nReply *Yes*, *No*, or *N/A*.`,
                    instruction: "SEND this next task to the staff and wait for their reply.",
                };
            }

            return { status: "error", message: "Unexpected response. Please try again." };
        } catch (error: any) {
            console.error("[ChecklistRespondTool] Error:", error.message);
            return {
                status: "error",
                message: "I couldn't record your response. Please try again.",
            };
        }
    }
}
