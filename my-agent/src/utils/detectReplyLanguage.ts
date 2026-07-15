/**
 * Detect the language Miya must reply in for this turn.
 * Uses the latest clear user message; short/ambiguous replies keep the prior language.
 */
import type { ChatMessage } from "lua-cli";
import { extractLastUserText, extractMessageText } from "./extractLastUserText";

export type ReplyLanguage =
    | "english"
    | "french"
    | "arabic"
    | "darija"
    | "spanish"
    | "portuguese"
    | "german";

const ARABIC_RE = /[\u0600-\u06FF\u0750-\u077F]/;
const DARIJA_LATIN_RE =
    /\b(bghit|daba|chi|wach|3ndi|salina|khdmti|safi|wakha|bzzaf|nshrou|khlass|fin|mzyan|smahli|labas)\b/i;
const FRENCH_RE =
    /\b(bonjour|bonsoir|merci|s['']il\s+vous|qu['']est|aujourd['']hui|commande|r[eé]servation|j['']aimerais|je\s+(veux|voudrais|dois|suis|peux)|tu\s+(peux|veux)|nous\s+(avons|devons)|facture|rappel|rendez[- ]?vous|pointage|pointer|hier|demain|s'il\s+te|est[- ]ce\s+que|pas\s+de|il\s+faut)\b/i;
const FRENCH_ACCENT_RE = /[àâäéèêëïîôùûüçœæ]/i;
const SPANISH_RE =
    /\b(hola|buenos\s+d[ií]as|gracias|quiero|necesito|por\s+favor|reserva|mañana)\b/i;
const PORTUGUESE_RE =
    /\b(ol[aá]|obrigad[oa]|preciso|por\s+favor|bom\s+dia|boa\s+tarde)\b/i;
const GERMAN_RE =
    /\b(hallo|guten\s+(tag|morgen|abend)|danke|bitte|ich\s+(brauche|möchte|will))\b/i;
const ENGLISH_RE =
    /\b(the|this|that|with|from|have|has|want|need|please|could|would|should|what|where|when|how|thanks?|hello|hi\b|clock|invoice|order|repair|reminder|schedule|shift|staff|manager|we\s+need|can\s+you|let\s+me|i\s+(want|need|have|am|will)|you\s+(can|are|have)|is\s+there|are\s+there)\b/i;

/** Single tokens / ack that must NOT flip the conversation language. */
const AMBIGUOUS_RE =
    /^(ok|okay|k|yes|no|yep|nope|oui|non|merci|thanks?|thx|👍|👎|✅|❌|🙏|😊|🙂|ok\.|yes\.|no\.|d['']accord|bien|parfait|cool|sure|yeah|yup|nah|mm+|hmm+|اوكي|حسنا|شكرا|لا|نعم|واخا|صافي)[\s!.?]*$/i;

const LABEL: Record<ReplyLanguage, string> = {
    english: "English",
    french: "French",
    arabic: "Arabic (Modern Standard)",
    darija: "Moroccan Darija",
    spanish: "Spanish",
    portuguese: "Portuguese",
    german: "German",
};

export function languageLabel(lang: ReplyLanguage): string {
    return LABEL[lang];
}

export function detectLanguageFromText(text: string): ReplyLanguage | null {
    const t = (text || "").trim();
    if (!t || t.length < 2) return null;
    if (AMBIGUOUS_RE.test(t)) return null;

    if (ARABIC_RE.test(t)) {
        // Light Darija cue in Arabic script
        if (/بغيت|دابا|واش|شحال|صافي|واخا|حيت/.test(t)) return "darija";
        return "arabic";
    }
    if (DARIJA_LATIN_RE.test(t)) return "darija";
    if (SPANISH_RE.test(t)) return "spanish";
    if (PORTUGUESE_RE.test(t)) return "portuguese";
    if (GERMAN_RE.test(t)) return "german";

    const hasFrench = FRENCH_RE.test(t);
    const hasEnglish = ENGLISH_RE.test(t);

    // Accented French without English → French
    if (FRENCH_ACCENT_RE.test(t) && !hasEnglish) return "french";
    if (hasFrench && !hasEnglish) return "french";
    if (hasEnglish && !hasFrench) return "english";
    if (hasFrench && hasEnglish) {
        const frHits = (t.match(FRENCH_RE) || []).length + (FRENCH_ACCENT_RE.test(t) ? 1 : 0);
        const enHits = (t.match(ENGLISH_RE) || []).length;
        if (frHits > enHits) return "french";
        if (enHits > frHits) return "english";
        return "english";
    }

    // Latin letters with no clear cues — treat as English (default for Mizan EN openers)
    if (/[a-zA-Z]{3,}/.test(t) && !FRENCH_ACCENT_RE.test(t)) return "english";

    return null;
}

/**
 * Walk recent user texts (newest first). Use the latest clear language;
 * if the latest message is ambiguous, keep the previous clear language.
 */
export function resolveReplyLanguage(
    messages: Array<ChatMessage | Record<string, unknown>>,
): { language: ReplyLanguage; sticky: boolean; sourceText: string } {
    const texts: string[] = [];
    for (const msg of messages) {
        const t = extractMessageText(msg as ChatMessage);
        if (t) texts.push(t);
    }
    // Prefer extractLastUserText for the tip of the turn
    const last = extractLastUserText(messages as ChatMessage[]) || texts[texts.length - 1] || "";

    const lastLang = detectLanguageFromText(last);
    if (lastLang) {
        return { language: lastLang, sticky: false, sourceText: last };
    }

    // Sticky: scan older messages newest→oldest for a clear language
    for (let i = texts.length - 1; i >= 0; i--) {
        const lang = detectLanguageFromText(texts[i]);
        if (lang) {
            return { language: lang, sticky: true, sourceText: texts[i] };
        }
    }

    return { language: "english", sticky: true, sourceText: last };
}

export function buildLanguageDirective(language: ReplyLanguage, sticky: boolean): string {
    const label = languageLabel(language);
    const stickyNote = sticky
        ? "The latest user message is short/ambiguous — KEEP the conversation language above (do not switch)."
        : "The latest user message clearly uses this language — reply in it for THIS turn.";

    return [
        `[REPLY LANGUAGE — NON-NEGOTIABLE]`,
        `Reply language for THIS turn: ${label}.`,
        stickyNote,
        `If the conversation started in English, stay in English until the user writes a clear sentence in another language.`,
        `If they switch mid-conversation (e.g. English → French), switch from that message onward.`,
        `Do NOT use restaurant/profile language settings. Do NOT drift to French because the business is in Morocco.`,
        `Do NOT reply in French when the user is writing in English.`,
        `Tool/system text may be English — still answer the user in ${label}.`,
    ].join("\n");
}
