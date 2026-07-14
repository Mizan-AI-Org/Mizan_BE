/**
 * Staff companion intents — "what should I do next" / coaching entry points.
 */

export function isWhatShouldIDoNextAsk(text: string): boolean {
  const t = (text || "").trim();
  if (!t || t.length < 5) return false;
  if (
    /\b(what\s+should\s+i\s+do\s+next|what'?s\s+next|what\s+do\s+i\s+do\s+next|what\s+now|next\s+(?:task|step|action)|que\s+faire\s+(?:maintenant|ensuite)|شنو\s+ندير|ماذا\s+أفعل|show\s+(?:me\s+)?(?:my\s+)?(?:next\s+)?tasks?|what\s+are\s+my\s+(?:tasks|priorities)\s+(?:today|now))\b/i.test(
      t,
    )
  ) {
    return true;
  }
  if (/^(what\s+next|next\??|and\s+now\??)\s*[.!?]*$/i.test(t)) return true;
  return false;
}
