/**
 * Shared safety-incident intent detection for WhatsApp + Lua preprocessors.
 * Tolerates "broke glass" (not just "broken glass") so Django/Lua own real reports.
 */

/** Clear safety / hazard reports — NOT routine equipment repairs. */
export const SAFETY_INCIDENT_RE =
  /\b(?:broke|broken|shatter(?:ed)?|smash(?:ed)?)\s+glass|glass\s+(?:broke|broken|shatter(?:ed)?|on\s+(?:the\s+)?(?:floor|ground)|(?:at|in|by|near)\s+the)|glass\s+shard|shards?\s+of\s+glass|verre\s+(?:cassé|brisé)|bris\s+de\s+verre|wet\s+floor|spill(?:ed)?\s+(?:on\s+)?(?:the\s+)?floor|customer\s+slipp?ed|guest\s+slipp?ed|someone\s+slipp?ed|slipp?ed\s+on|fell\s+(?:down|on)|injur(?:y|ed)|bleed(?:ing)?|burn(?:ed|t)?|fire|smoke|gas\s+(?:leak|smell)|food\s+poison|harass(?:ment)?|theft|robbery|unconscious|table\s+\d+.{0,60}glass)\b/i;

export function isSafetyIncidentMessage(text: string): boolean {
  const t = (text || "").trim();
  if (!t || t.length < 6) return false;
  // Exclude clear equipment-repair phrasing without hazard cues
  if (
    /\b(fridge|freezer|oven|dishwasher|ac\b|air\s*con|wc|toilet|plumbing)\b/i.test(
      t,
    ) &&
    !/\b(glass|slip|injur|fire|smoke|bleed|burn|gas\s+leak|broke|broken|shatter)\b/i.test(
      t,
    )
  ) {
    return false;
  }
  return SAFETY_INCIDENT_RE.test(t);
}

/** Fake incident-denial apologies the model invents instead of calling the API. */
export const FAKE_INCIDENT_REPORT_RE =
  /\b(unable to report the incident|couldn['']t report the incident|failed to report (?:the )?incident|incident at this (?:time|moment)|cannot report the incident|can['']t report the incident|contact your manager directly about)\b/i;

export function looksLikeFakeIncidentReport(text: string): boolean {
  return FAKE_INCIDENT_REPORT_RE.test(String(text || ""));
}
