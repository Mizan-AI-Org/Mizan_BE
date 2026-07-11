/**
 * Shared clock-in intent / anti-cash-float helpers used by every Miya specialist.
 * Keeps WhatsApp attendance on: location share → geofence — never opening float.
 */

export const CLOCK_IN_RE =
  /\b(clock\s+me\s+in|clock[\s-]?in|clockin|pointer|pointage|start my shift|i['']?m here|arriver|سجل دخول|بغيت نبدا|بغيت نبدا الخدمة|nbeda lkhedma|(?:staff\s+)?(?:wants?|needs?)\s+to\s+clock\s*in)\b/i;

/** Bot wrongly gating clock-in behind cash drawer / opening float. */
export const CASH_BEFORE_CLOCK_IN_RE =
  /\b(opening\s+float|cash\s+(?:is\s+)?(?:currently\s+)?in\s+the\s+drawer|how\s+much\s+cash|to\s+clock\s+you\s+in|need\s+that\s+to\s+clock|float\s*\(.*drawer|cash\s+count.*clock|clock.*(?:opening\s+)?float)\b/i;

/** Fake / broken clock-in apologies the model invents. */
export const FAKE_CLOCK_IN_OUTAGE_RE =
  /\b(trouble with the clock[- ]?in|temporary system issue preventing clock[- ]?ins?|clock[- ]?in system right now|unable to clock you in|sorry[,.]?\s*I was unable to clock you in|having a bit of trouble with (?:the )?clock|technical issue(?:\s+\w+){0,12}\s+clock|couldn['']t clock you in|encountered a technical issue.{0,80}clock|please try again later or contact your manager for assistance)\b/i;

/** Fake shift-fetch apologies (Space invents these instead of calling my_shifts). */
export const FAKE_SHIFT_FETCH_RE =
  /\b(trouble fetching your shift|having a little trouble fetching your shift|couldn['']t (?:fetch|load|get) your shift|unable to (?:fetch|load|get) your shift|shift details right now)\b/i;

export function isClockInMessage(text: string): boolean {
  const lower = text.toLowerCase().trim();
  if (!lower) return false;
  if (CLOCK_IN_RE.test(lower)) return true;
  if (lower.includes("want to clock in")) return true;
  if (lower.includes("clock me in")) return true;
  if (lower.includes("hi miya") && lower.includes("clock")) return true;
  return false;
}

export function looksLikeCashBeforeClockInAsk(text: string): boolean {
  return CASH_BEFORE_CLOCK_IN_RE.test(String(text || ""));
}

export function looksLikeFakeClockInOutage(text: string): boolean {
  return FAKE_CLOCK_IN_OUTAGE_RE.test(String(text || ""));
}

export function looksLikeFakeShiftFetch(text: string): boolean {
  return FAKE_SHIFT_FETCH_RE.test(String(text || ""));
}

/** User follow-up after Miya wrongly asked for float (amount / "I don't know"). */
export function looksLikeCashClockInFollowUp(text: string): boolean {
  const t = String(text || "").trim();
  if (!t) return false;
  if (/^(i\s+)?(don['']?t|do\s+not)\s+know\b/i.test(t)) return true;
  if (/^(je\s+)?(ne\s+)?sais\s+pas\b/i.test(t)) return true;
  if (/^\d+([.,]\d+)?\s*(mad|dh|€|\$|eur|usd)?\.?$/i.test(t)) return true;
  if (/^(opening\s+)?float\s*[:=]?\s*\d+/i.test(t)) return true;
  return false;
}

export function extractAssistantTexts(messages: Array<Record<string, unknown>>): string[] {
  const out: string[] = [];
  for (const msg of messages) {
    const role = String(msg.role || msg.sender || msg.from || "").toLowerCase();
    const type = String(msg.type || "").toLowerCase();
    const isAssistant =
      role === "assistant" ||
      role === "bot" ||
      role === "ai" ||
      type === "assistant" ||
      type === "bot" ||
      msg.is_assistant === true;
    if (!isAssistant) continue;
    let text = "";
    if (typeof msg.text === "string") text = msg.text;
    else if (msg.text && typeof msg.text === "object") {
      const nested = msg.text as Record<string, unknown>;
      if (typeof nested.body === "string") text = nested.body;
    } else if (typeof msg.body === "string") text = msg.body;
    else if (typeof msg.content === "string") text = msg.content;
    else if (typeof msg.response === "string") text = msg.response;
    if (text.trim()) out.push(text.trim());
  }
  return out;
}

/**
 * True when this turn must run staff_clock_in (never cash_reconciliation / LLM float ask).
 */
export function shouldForceStaffClockIn(
  lastUserText: string,
  messages: Array<Record<string, unknown>>,
  hasLocation: boolean,
): boolean {
  if (hasLocation && !String(lastUserText || "").trim()) return true;
  if (isClockInMessage(lastUserText)) return true;

  const assistants = extractAssistantTexts(messages);
  const recentAsk = [...assistants].reverse().find((t) => looksLikeCashBeforeClockInAsk(t));
  if (recentAsk && looksLikeCashClockInFollowUp(lastUserText)) return true;
  if (recentAsk && isClockInMessage(lastUserText)) return true;
  return false;
}

export function shareLocationClockInMessage(channel: string): string {
  const isWeb = /web|luapop|pop|dashboard/i.test(String(channel || ""));
  return isWeb
    ? "To clock in, open Time Clock from your staff menu and tap Clock In (allow location when prompted)."
    : "Share your location to clock in.";
}
