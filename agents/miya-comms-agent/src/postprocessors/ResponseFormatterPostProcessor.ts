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

    // Strip any leaked technical jargon
    for (const pattern of JARGON_PATTERNS) {
      formatted = formatted.replace(pattern, "");
    }

    for (const { re, replacement } of SPECIALIST_NAME_PATTERNS) {
      formatted = formatted.replace(re, replacement);
    }

    // Never let the model invent fake clock-in outages — replace with actionable copy.
    const fakeClockIn =
      /\b(trouble with the clock[- ]?in|temporary system issue preventing clock[- ]?ins?|clock[- ]?in system right now|unable to clock you in|sorry[,.]?\s*I was unable to clock you in|having a bit of trouble with (?:the )?clock)\b/i.test(
        formatted,
      );
    if (fakeClockIn) {
      const isWeb = /web|luapop|pop|dashboard/i.test(String(channel || ""));
      formatted = isWeb
        ? "To clock in, open Time Clock from your staff menu and tap Clock In (allow location when prompted)."
        : "Share your location to clock in.";
    }

    // Never ask for cash drawer / opening float instead of location on clock-in.
    if (
      /\b(to clock in[,.]?\s*I need to know how much cash|opening float|cash (?:is )?currently in the drawer|how much cash is in the drawer)\b/i.test(
        formatted,
      )
    ) {
      formatted = "Share your location to clock in.";
    }

    // Never invent fake incident failures (e.g. after wrong_tool on "broken glass")
    if (
      /\b(unable to report the incident|couldn['']t report the incident|failed to report (?:the )?incident|incident at this time)\b/i.test(
        formatted,
      )
    ) {
      formatted =
        "Please say that again in one short line (e.g. \"Broken glass at table 44\") and I'll log it as a safety report right away. If anyone is hurt, tell a manager on duty now.";
    }

    // Never invent fake "noted for manager / confirmation card" without staff_request
    if (
      /\b(confirmation card will be shown|noted that for your manager|preparing to (?:let your manager know|inform your manager))\b/i.test(
        formatted,
      )
    ) {
      formatted =
        "Please say that again in one short line (e.g. \"Tell my manager I haven't received last week's wages\") and I'll pass it to your manager right away.";
    }

    // Never invent fake checklist / tasks outages
    if (
      /\b(technical issue trying to (?:fetch|start|load) your (?:tasks|checklist)|unable to (?:fetch|start|load) your (?:tasks|checklist)|trouble (?:fetching|starting|loading) your (?:tasks|checklist)|couldn['']?t get your checklist started|could not get your checklist started|oops!?\s*looks like i couldn['']?t get your checklist)\b/i.test(
        formatted,
      )
    ) {
      formatted =
        "Say *what are my tasks* to preview, or *start checklist* once you're clocked in — I'll load them right away.";
    }

    // Staff channel: strip manager/dashboard jargon the model may regurgitate
    if (audience === "staff") {
      for (const pattern of MANAGER_JARGON_PATTERNS) {
        formatted = formatted.replace(pattern, "");
      }
      formatted = formatted.replace(/\bopen the app\b/gi, "message me here");
      formatted = formatted.replace(/\bcheck your dashboard\b/gi, "I'll keep you posted here");
    }

    // Clean up artifacts from jargon removal (double spaces, empty parens)
    formatted = formatted
      .replace(/\(\s*\)/g, "")
      .replace(/\s{2,}/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();

    // Channel-specific formatting
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

  // WhatsApp has a practical limit of ~4096 chars
  if (result.length > 3800) {
    const truncatePoint = result.lastIndexOf("\n", 3600);
    if (truncatePoint > 2000) {
      result =
        result.slice(0, truncatePoint) +
        "\n\n_...reply 'more' for the rest._";
    }
  }

  // Convert markdown headers to WhatsApp bold
  result = result.replace(/^###?\s+(.+)$/gm, "*$1*");

  // Convert markdown bold to WhatsApp bold
  result = result.replace(/\*\*(.+?)\*\*/g, "*$1*");

  // Convert markdown links [text](url) to text: url (WhatsApp doesn't render markdown links)
  result = result.replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1: $2");

  return result;
}

function formatForManagerPop(text: string): string {
  // LuaPop / dashboard embed — operational tone, richer structure OK
  return text;
}

export default responseFormatter;
