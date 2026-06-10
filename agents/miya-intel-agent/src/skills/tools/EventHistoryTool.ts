/**
 * Closes the Data API read loop — queries forecasting-events,
 * staff-management-events, and user-events collections that the
 * webhooks already write to. Gives Miya operational memory across
 * conversations.
 */
import { LuaTool, User, Data } from "lua-cli";
import { z } from "zod";
import { noContextError } from "./_common/errors";

export default class EventHistoryTool implements LuaTool {
  name = "search_event_history";
  description =
    "Search past operational events (forecasting, staff management, user events) " +
    "using semantic search. Use this when the manager asks about patterns, recent " +
    "events, what happened with a staff member, incident history, or trends. " +
    "Examples: 'what incidents happened this week?', 'any patterns with late clock-ins?', " +
    "'show me recent staff events for Ahmed'.";

  inputSchema = z.object({
    query: z.string().describe(
      "Natural language search query (e.g. 'late clock-ins last week', 'incidents in kitchen', 'Ahmed attendance')"
    ),
    collection: z
      .enum(["forecasting-events", "staff-management-events", "user-events", "all"])
      .default("all")
      .describe("Which event collection to search. Default 'all' searches across all collections."),
    limit: z
      .number()
      .optional()
      .default(10)
      .describe("Max results to return (default 10)"),
    min_relevance: z
      .number()
      .optional()
      .default(0.6)
      .describe("Minimum similarity score 0-1 (default 0.6)"),
    restaurantId: z
      .string()
      .optional()
      .describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
  });

  async execute(input: z.infer<typeof this.inputSchema>) {
    const user = await User.get();
    if (!user) return noContextError();

    const userData = (user as any).data || {};
    const profile = (user as any)._luaProfile || {};
    const restaurantId =
      input.restaurantId ||
      (user as any).restaurantId ||
      userData.restaurantId ||
      profile.restaurantId;

    if (!restaurantId) {
      return noContextError({ hint: "Restaurant ID needed to search event history." });
    }

    const collections =
      input.collection === "all"
        ? ["forecasting-events", "staff-management-events", "user-events"]
        : [input.collection];

    const allResults: Array<{
      collection: string;
      relevance: number;
      data: Record<string, unknown>;
    }> = [];

    for (const coll of collections) {
      try {
        const results = await Data.search(
          coll,
          `${input.query} restaurant:${restaurantId}`,
          input.limit || 10,
          input.min_relevance || 0.6
        );

        for (const entry of results) {
          allResults.push({
            collection: coll,
            relevance: Math.round((entry.score || 0) * 100) / 100,
            data: {
              id: entry.id,
              type: entry.type || entry.event_type,
              summary: entry.summary || entry.description || entry.title,
              timestamp: entry.createdAt || entry.timestamp,
              ...(entry.staff_name ? { staff_name: entry.staff_name } : {}),
              ...(entry.priority ? { priority: entry.priority } : {}),
              ...(entry.category ? { category: entry.category } : {}),
              ...(entry.status ? { status: entry.status } : {}),
            },
          });
        }
      } catch {
        // Collection may not exist yet — skip silently
      }
    }

    allResults.sort((a, b) => b.relevance - a.relevance);
    const topResults = allResults.slice(0, input.limit || 10);

    if (topResults.length === 0) {
      return {
        status: "success",
        message: "No matching events found. The event history may be empty or the query didn't match.",
        events: [],
        total: 0,
        miya_directive:
          "Tell the user you couldn't find matching events in their language. Suggest broadening the search or trying different keywords.",
      };
    }

    return {
      status: "success",
      message: `Found ${topResults.length} matching event(s).`,
      events: topResults,
      total: topResults.length,
      query: input.query,
      miya_directive:
        "Summarize the events for the manager in their language. Highlight patterns if you see them (e.g. repeated late clock-ins, recurring incidents). Offer to take action on any findings.",
    };
  }
}
