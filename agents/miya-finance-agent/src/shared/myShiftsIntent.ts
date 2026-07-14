/**
 * Shared "when is my shift" intent detection for WhatsApp + Lua preprocessors.
 * Tolerates common typos (e.g. "shit" → shift) so staff still get real schedule data.
 *
 * Use RegExp literals only — never build `\s` via normal JS strings (they become `s`).
 */

/** shift / shifts / shit / shif typos */
const SHIFT = String.raw`(?:shifts?|shits?|shifs?|shiifts?)`;

export const MY_SHIFTS_RE = new RegExp(
  String.raw`\b(` +
    String.raw`my\s+` +
    SHIFT +
    String.raw`|my\s+schedule|` +
    String.raw`when\s+(?:is|are|was)\s+my\s+(?:` +
    SHIFT +
    String.raw`|work|schedule)|` +
    String.raw`what(?:'s|\s+is|\s+are)\s+my\s+(?:` +
    SHIFT +
    String.raw`|schedule|work)|` +
    String.raw`what\s+time\s+(?:is\s+)?(?:my\s+)?(?:` +
    SHIFT +
    String.raw`|work)|` +
    String.raw`when\s+do\s+i\s+work|` +
    SHIFT +
    String.raw`\s+(?:today|tomorrow)|schedule\s+(?:today|tomorrow)|` +
    String.raw`do\s+i\s+(?:work|have\s+(?:a\s+)?shift)|` +
    String.raw`am\s+i\s+(?:working|scheduled)|` +
    String.raw`horaire|mes\s+` +
    SHIFT +
    String.raw`|mon\s+planning|شيفت|دوامي|جدول` +
    String.raw`)\b`,
  "i",
);

const SHIFT_TYPO_WORDS = new Set([
  "shift",
  "shifts",
  "shit",
  "shif",
  "shiift",
  "work",
  "schedule",
  "duty",
  "rota",
  "service",
  "turn",
]);

export function isMyShiftsAsk(text: string): boolean {
  const t = (text || "").trim();
  if (!t || t.length < 5) return false;
  if (MY_SHIFTS_RE.test(t)) return true;

  const typoMatch = t.match(
    /\bwhen\s+(?:is|are)\s+my\s+(\w+)\s+(?:today|tomorrow|tonight)\b/i,
  );
  if (typoMatch && SHIFT_TYPO_WORDS.has(typoMatch[1].toLowerCase())) {
    return true;
  }

  if (
    /\bwhat\s+time\s+(?:am\s+)?i\s+(?:working|on)\s+(?:today|tomorrow)\b/i.test(t)
  ) {
    return true;
  }

  // Soft match: "okay. when is my shift today and tomorrow"
  if (
    /\bwhen\s+(?:is|are)\s+my\s+\w+/i.test(t) &&
    /\b(today|tomorrow|tonight|this\s+week|next\s+week)\b/i.test(t)
  ) {
    return true;
  }

  return false;
}
