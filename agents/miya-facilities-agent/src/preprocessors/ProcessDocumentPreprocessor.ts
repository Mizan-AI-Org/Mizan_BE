/**
 * When a manager attaches a Processes & Tasks document in LuaPop/dashboard,
 * import checklists immediately instead of waiting for the LLM to pick a tool.
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import ApiService from "../services/ApiService";
import { extractLastUserText } from "../utils/extractLastUserText";
import { resolveTenantForUser } from "../utils/resolveTenantForUser";
import { resolveMessageAudience } from "../utils/resolveMessageAudience";

const PROCESS_INTENT_RE =
    /\b(process(?:es)?|checklist(?:s)?|task\s*template(?:s)?|sop|procedures?|import\s+process|processes\s*&\s*tasks|opening|closing)\b/i;

function extractDocumentAttachment(messages: ChatMessage[]): {
    url: string;
    mimeType: string;
    fileName?: string;
} | null {
    for (const msg of messages.slice().reverse()) {
        if (msg.type === "file") {
            const mime = String((msg as { mimeType?: string }).mimeType || "").toLowerCase();
            if (mime.startsWith("image/")) continue;
            const url = String((msg as { data?: string }).data || "").trim();
            if (url) {
                const fileName = String((msg as { fileName?: string }).fileName || "").trim() || undefined;
                return { url, mimeType: mime || "application/octet-stream", fileName };
            }
        }
    }
    return null;
}

function shouldImportProcesses(messages: ChatMessage[], channel: string): boolean {
    if (resolveMessageAudience(channel) !== "manager") return false;
    const doc = extractDocumentAttachment(messages);
    if (!doc) return false;

    const text = extractLastUserText(messages) || "";
    if (PROCESS_INTENT_RE.test(text)) return true;

    const name = (doc.fileName || "").toLowerCase();
    if (/process|checklist|template|sop|opening|closing/.test(name)) return true;

    // Manager attached a doc on dashboard with little/no text — still try import.
    return !text.trim() || text.length < 120;
}

function userMessage(data: Record<string, unknown>, fallback: string): string {
    return String(data.message_for_user || fallback).trim();
}

export const processDocumentPreprocessor = new PreProcessor({
    name: "process-document-import",
    description:
        "Auto-import Processes & Tasks checklists when managers attach CSV/PDF/DOCX/XLSX in LuaPop.",
    priority: 107,

    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        if (!shouldImportProcesses(messages, channel)) {
            return { action: "proceed" as const };
        }

        const attachment = extractDocumentAttachment(messages);
        if (!attachment) return { action: "proceed" as const };

        const tenant = await resolveTenantForUser(user);
        if (!tenant.restaurantId) return { action: "proceed" as const };

        const note = extractLastUserText(messages) || "";
        const api = new ApiService();

        console.log(
            `[ProcessDocumentPreprocessor] importing processes channel=${channel} restaurant=${tenant.restaurantId}`,
        );

        let data: Record<string, unknown>;
        try {
            data = (await api.importProcessTemplatesForAgent(tenant.restaurantId, {
                documentUrl: attachment.url,
                contentType: attachment.mimeType,
                fileName: attachment.fileName,
                note,
            })) as Record<string, unknown>;
        } catch (err: unknown) {
            const em = err instanceof Error ? err.message : String(err);
            console.error("[ProcessDocumentPreprocessor] import threw:", em);
            return { action: "proceed" as const };
        }

        if (data?.success === false && !data?.created) {
            const msg = userMessage(
                data,
                "I couldn't import processes from that file. Try CSV or Excel with process names and task steps, or tell me which checklist to create.",
            );
            return { action: "block" as const, response: msg };
        }

        const created = (data.created || []) as Array<{ id?: string; name?: string }>;
        if (created.length > 0) {
            return {
                action: "block" as const,
                response: userMessage(
                    data,
                    `Imported ${created.length} process(es) to Processes & Tasks → Templates.`,
                ),
                metadata: {
                    process_import_count: created.length,
                    template_ids: created.map((c) => c.id).filter(Boolean),
                },
            };
        }

        return {
            action: "block" as const,
            response: userMessage(
                data,
                "I read the file but didn't create any new processes (they may already exist). Check Processes & Tasks → Templates.",
            ),
        };
    },
});

export default processDocumentPreprocessor;
