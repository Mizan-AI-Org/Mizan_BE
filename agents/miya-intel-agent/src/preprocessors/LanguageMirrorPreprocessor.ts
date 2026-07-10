/**
 * Injects a hard [REPLY LANGUAGE] directive every turn so Miya mirrors the
 * user's latest clear language (English stays English; mid-chat switches stick).
 */
import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";
import {
    buildLanguageDirective,
    resolveReplyLanguage,
} from "../utils/detectReplyLanguage";
import { extractLastUserText, extractMessageText } from "../utils/extractLastUserText";

export const languageMirrorPreprocessor = new PreProcessor({
    name: "language-mirror",
    description:
        "Detects the user's reply language each turn and forces Miya to answer in that language.",
    priority: 2,

    execute: async (_user: UserDataInstance, messages: ChatMessage[], _channel: string) => {
        const last = extractLastUserText(messages);
        if (!last.trim()) {
            return { action: "proceed" as const };
        }

        const { language, sticky } = resolveReplyLanguage(messages);
        const directive = buildLanguageDirective(language, sticky);

        console.log(
            `[LanguageMirror] reply_language=${language} sticky=${sticky} last=${JSON.stringify(last.slice(0, 60))}`,
        );

        // Prefix only the newest user-visible text message
        let lastTextIdx = -1;
        for (let i = messages.length - 1; i >= 0; i--) {
            if (extractMessageText(messages[i])) {
                lastTextIdx = i;
                break;
            }
        }

        if (lastTextIdx < 0) {
            return {
                action: "proceed" as const,
                modifiedMessage: [
                    { type: "text" as const, text: `${directive}\n\n${last}` } as ChatMessage,
                    ...messages,
                ],
            };
        }

        const modifiedMessages = messages.map((msg, idx) => {
            if (idx !== lastTextIdx) return msg;
            const text = extractMessageText(msg);
            if (!text) return msg;
            if (text.includes("[REPLY LANGUAGE — NON-NEGOTIABLE]")) return msg;
            const raw =
                typeof (msg as { text?: unknown }).text === "string"
                    ? (msg as { text: string }).text
                    : text;
            return { ...msg, type: "text" as const, text: `${directive}\n\n${raw}` };
        });

        return { action: "proceed" as const, modifiedMessage: modifiedMessages };
    },
});

export default languageMirrorPreprocessor;
