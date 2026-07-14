/**
 * Shared "when is my shift" intent detection for WhatsApp + Lua preprocessors.
 * Tolerates common typos (e.g. "shit" → shift) so staff still get real schedule data.
 */

export const MY_SHIFTS_WORD = String.raw`sh(?:i[fpt]|it)s?`;

export const MY_SHIFTS_RE = new RegExp(
  String.raw`\b(` +
    `my\s+${MY_SHIFTS_WORD}|my\s+schedule|` +
    `when\s+(?:is|are|was)\s+my\s+(?:${MY_SHIFTS_WORD}|work|schedule)|` +
    `what(?:'s|\s+is|\s+are)\s+my\s+(?:${MY_SHIFTS_WORD}|schedule|work)|` +
    `what\s+time\s+(?:is\s+)?(?:my\s+)?(?:${MY_SHIFTS_WORD}|work)|` +
    `when\s+do\s+i\s+work|` +
    `${MY_SHIFTS_WORD}\s+(?:today|tomorrow)|schedule\s+(?:today|tomorrow)|` +
    `do\s+i\s+(?:work|have\s+(?:a\s+)?shift)|` +
    `am\s+i\s+(?:working|scheduled)|` +
    `horaire|mes\s+${MY_SHIFTS_WORD}|mon\s+planning|شيفت|دوامي|جدول` +
    `)\b`,
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

  return false;
}
