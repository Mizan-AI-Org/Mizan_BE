/**
 * Shared checklist intent detection for WhatsApp + Lua preprocessors.
 */

export const START_CHECKLIST_RE =
  /\b(start\s+(my\s+)?(check\s*lists?|tasks|checklists?)|begin\s+(my\s+)?(check\s*lists?|tasks|checklists?)|checklists?\s+start|get\s+(my\s+)?checklist\s+started|load\s+(my\s+)?(checklist|tasks)|(?:staff\s+)?(?:wants?|needs?)\s+to\s+start\s+(?:their\s+)?(?:checklist|tasks)|ابدأ\s*(المهام|القائمة)|demarrer\s+(la\s+)?checklists?|commencer\s+(la\s+)?checklists?)\b/i;

export const PREVIEW_TASKS_RE =
  /\b(what\s+(?:are\s+)+(?:my\s+)?tasks(?:\s+today)?|(?:my\s+)?tasks\s+today|show\s+(?:my\s+)?(?:tasks|check\s*lists?)|my\s+(?:tasks|check\s*lists?)|list\s+(?:my\s+)?(?:tasks|check\s*lists?)|ما\s+هي\s+مهامي|شنو\s+المهام|mes\s+t[aâ]ches(?:\s+aujourd['']?hui)?|voir\s+(?:ma\s+)?checklists?)\b/i;

/** Fake checklist-start apologies the model invents instead of calling checklist_starter. */
export const FAKE_CHECKLIST_START_RE =
  /\b(wasn['']?t able to start your checklist|was not able to start your checklist|unable to start your checklist|couldn['']?t start your checklist|could not start your checklist|technical (?:issue|snag).{0,60}checklist|checklist.{0,40}technical (?:issue|snag)|having trouble.{0,30}checklist|trouble starting your checklist|try again in a moment.{0,40}checklist)\b/i;

export function isStartChecklistMessage(text: string): boolean {
  const t = (text || "").trim();
  if (!t) return false;
  if (START_CHECKLIST_RE.test(t)) return true;
  const normalized = t
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const phrases = [
    "start checklist",
    "start my checklist",
    "start the checklist",
    "start task checklist",
    "begin checklist",
    "start my tasks",
    "run checklist",
    "do my checklist",
  ];
  return phrases.some((p) => normalized.includes(p));
}

export function isPreviewTasksMessage(text: string): boolean {
  return PREVIEW_TASKS_RE.test((text || "").trim());
}

export function looksLikeFakeChecklistStart(text: string): boolean {
  return FAKE_CHECKLIST_START_RE.test(String(text || ""));
}
