/**
 * Response Formatter PostProcessor
 *
 * Channel-aware formatting that adapts Miya's responses for
 * WhatsApp (staff) vs LuaPop/web (manager). Also enforces jargon
 * stripping and consistent branding.
 *
 * Priority: 10 (runs early, before any branding/disclaimer)
 */
import { PostProcessor, UserDataInstance } from "lua-cli";
import {
  MANAGER_JARGON_PATTERNS,
  resolveMessageAudience,
} from "../utils/resolveMessageAudience";
import { stripSystemContextBlocks } from "../utils/stripSystemContext";
import {
  isClockInMessage,
  looksLikeCashBeforeClockInAsk,
  looksLikeFakeClockInOutage,
  looksLikeFakeShiftFetch,
  shareLocationClockInMessage,
} from "../shared/clockInGuard";
import { looksLikeFakeIncidentReport } from "../shared/incidentIntent";
import { looksLikeFakeChecklistStart } from "../shared/checklistIntent";

const JARGON_PATTERNS = [
  /\baccess token\b/gi,
  /\bOAuth\b/g,
  /\bbearer\b/gi,
  /\bAPI\b/g,
  /\bendpoint\b/gi,
  /\bwebhook\b/gi,
  /\btoken expired\b/gi,
  /\binvalid token\b/gi,
  /\bsession expired\b/gi,
  /\b40[0-9]\b/g,
  /\b50[0-9]\b/g,
  /\(#\d+\)/g,
  /\bWhatsApp Cloud API\b/gi,
  /\bMeta Cloud\b/gi,
  /\bGraph API\b/gi,
  /\brate limit\b/gi,
  /\bstack trace\b/gi,
  /\bHTTP status\b/gi,
  /\bJSON\b/g,
  /\bundefined\b/g,
  /\bnull\b/g,
];

/** Never expose swarm internals — managers talk to one Miya. */
const SPECIALIST_NAME_PATTERNS: Array<{ re: RegExp; replacement: string }> = [
  { re: /\bMiya\s*[- ]?\s*HR\b/gi, replacement: "Miya" },
  { re: /\bMiya\s*[- ]?\s*Facilities\b/gi, replacement: "Miya" },
  { re: /\bMiya\s*[- ]?\s*Ops\b/gi, replacement: "Miya" },
  { re: /\bMiya\s*[- ]?\s*Operations\b/gi, replacement: "Miya" },
  { re: /\bMiya\s*[- ]?\s*Finance\b/gi, replacement: "Miya" },
  { re: /\bMiya\s*[- ]?\s*Comms\b/gi, replacement: "Miya" },
  { re: /\bMiya\s*[- ]?\s*Intel\b/gi, replacement: "Miya" },
  { re: /\bMiya\s*[- ]?\s*Space\b/gi, replacement: "Miya" },
  { re: /\bspecialist\s+agent\b/gi, replacement: "Miya" },
];

const responseFormatter = new PostProcessor({
  name: "miya-response-formatter",
  description:
    "Channel-aware formatting: staff tone on WhatsApp, manager tone on LuaPop, strips technical jargon",

  execute: async (
    user: UserDataInstance,
    message: string,
    response: string,
    channel: string
  ) => {
    let formatted = stripSystemContextBlocks(response);
    const audience = resolveMessageAudience(channel);
    const userMsg = String(message || "");

    for (const pattern of JARGON_PATTERNS) {
      formatted = formatted.replace(pattern, "");
    }

    for (const { re, replacement } of SPECIALIST_NAME_PATTERNS) {
      formatted = formatted.replace(re, replacement);
    }

    // Hard ban: never invent clock-in outages or gate clock-in behind cash float.
    // Also: never answer an incident photo/description with a clock-in apology.
    if (
      /\b(try clocking in again|would you like to try clocking in|clocking in again)\b/i.test(
        formatted,
      ) &&
      !isClockInMessage(userMsg)
    ) {
      formatted =
        'Got it — if you\'re reporting something on the floor, send a short description (e.g. "Broken glass at table 44") and I\'ll log it as an incident right away.';
    } else if (
      looksLikeFakeClockInOutage(formatted) ||
      looksLikeCashBeforeClockInAsk(formatted) ||
      (isClockInMessage(userMsg) &&
        /\b(cash|float|drawer|comptage|caisse)\b/i.test(formatted))
    ) {
      formatted = shareLocationClockInMessage(channel);
    }

    if (looksLikeFakeShiftFetch(formatted)) {
      formatted =
        'Say "when is my shift today and tomorrow?" again and I\'ll look up your schedule right away.';
    }

    if (looksLikeFakeIncidentReport(formatted)) {
      formatted =
        "Please say that again in one short line (e.g. \"Broken glass at table 44\") and I'll log it as a safety report right away. If anyone is hurt, tell a manager on duty now.";
    }

    if (
      /\b(confirmation card will be shown|noted that for your manager|preparing to (?:let your manager know|inform your manager)|correct recipient|final approval before anything is sent)\b/i.test(
        formatted,
      )
    ) {
      formatted =
        "Thanks — I've passed that on to your manager. They'll see it under *Human Resources* (Pending) and get back to you as soon as they can.";
    }

    if (looksLikeFakeChecklistStart(formatted)) {
      formatted =
        "Say *what are my tasks* to preview, or *start checklist* once you're clocked in — I'll load them right away.";
    }

    if (audience === "staff") {
      for (const pattern of MANAGER_JARGON_PATTERNS) {
        formatted = formatted.replace(pattern, "");
      }
      formatted = formatted.replace(/\bopen the app\b/gi, "message me here");
      formatted = formatted.replace(/\bcheck your dashboard\b/gi, "I'll keep you posted here");
    }

    formatted = formatted
      .replace(/\(\s*\)/g, "")
      .replace(/\s{2,}/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();

    if (audience === "staff") {
      formatted = formatForWhatsApp(formatted);
    } else {
      formatted = formatForManagerPop(formatted);
    }

    return { modifiedResponse: formatted };
  },
});

function formatForWhatsApp(text: string): string {
  let result = text;
  if (result.length > 3800) {
    const truncatePoint = result.lastIndexOf("\n", 3600);
    if (truncatePoint > 2000) {
      result =
        result.slice(0, truncatePoint) +
        "\n\n_...reply 'more' for the rest._";
    }
  }
  result = result.replace(/^###?\s+(.+)$/gm, "*$1*");
  result = result.replace(/\*\*(.+?)\*\*/g, "*$1*");
  result = result.replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1: $2");
  return result;
}

function formatForManagerPop(text: string): string {
  return text;
}

export default responseFormatter;
