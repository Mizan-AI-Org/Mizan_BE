/**
 * Manager copilot intents — sales / low stock / purchase recommendations.
 * Deterministic so Space cannot invent KPI numbers.
 */

export type ManagerCopilotKind = "sales_today" | "low_stock" | "recommend_purchases" | null;

export function classifyManagerCopilotAsk(text: string): ManagerCopilotKind {
  const t = (text || "").trim();
  if (!t || t.length < 6) return null;

  if (
    /\b(today'?s?\s+sales|sales\s+today|how\s+(?:are|did)\s+(?:we|sales)|chiffre\s+d['']affaires|ventes\s+(?:d['']?aujourd|today)|how\s+much\s+(?:did\s+we\s+)?(?:make|sell)|sales\s+report|what\s+are\s+(?:our\s+)?sales)\b/i.test(
      t,
    )
  ) {
    return "sales_today";
  }

  if (
    /\b(running\s+low|low\s+stock|stock\s+low|what(?:'s|\s+is)\s+low|which\s+(?:products?|items?)\s+(?:are\s+)?(?:running\s+)?low|reorder\s+level|below\s+par|out\s+of\s+stock|rupture\s+de\s+stock|stock\s+bas|what\s+needs\s+reordering)\b/i.test(
      t,
    )
  ) {
    return "low_stock";
  }

  if (
    /\b(recommend\s+(?:today'?s?\s+)?(?:purchases?|orders?)|what\s+should\s+i\s+(?:buy|order|reorder)|suggest\s+(?:a\s+)?(?:purchase|order)|achat\s+du\s+jour|que\s+commander|what\s+to\s+(?:buy|order)\s+today)\b/i.test(
      t,
    )
  ) {
    return "recommend_purchases";
  }

  return null;
}
