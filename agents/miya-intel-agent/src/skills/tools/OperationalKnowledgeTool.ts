/**
 * Knowledge base tool using Lua Data API for semantic search.
 * Stores and retrieves SOPs, policies, menu info, training materials.
 * Replaces the static restaurant_knowledge tool with a dynamic,
 * per-tenant knowledge base powered by vector search.
 */
import { LuaTool, User, Data } from "lua-cli";
import { z } from "zod";
import { noContextError, validationError } from "./_common/errors";

export default class OperationalKnowledgeTool implements LuaTool {
  name = "knowledge_base";
  description =
    "Search or manage the restaurant's operational knowledge base (SOPs, menus, policies, " +
    "training materials, procedures). Use 'search' when staff asks 'how do I close the kitchen?', " +
    "'what allergens are in the Caesar salad?', 'what's the opening procedure?'. Use 'add' when " +
    "the manager wants to store a new procedure or policy.";

  inputSchema = z.object({
    action: z
      .enum(["search", "add", "update", "list"])
      .describe(
        "search: semantic search for knowledge. add: store new knowledge. update: modify existing. list: show all in a category."
      ),
    query: z
      .string()
      .optional()
      .describe("Search query (natural language). Required for 'search'."),
    category: z
      .enum([
        "sop",
        "menu",
        "policy",
        "training",
        "safety",
        "hygiene",
        "equipment",
        "general",
      ])
      .optional()
      .describe("Knowledge category for filtering or adding."),
    title: z
      .string()
      .optional()
      .describe("Title of the knowledge entry (for 'add' / 'update')."),
    content: z
      .string()
      .optional()
      .describe("Full content/procedure text (for 'add' / 'update')."),
    entry_id: z
      .string()
      .optional()
      .describe("Entry ID for 'update' action."),
    restaurantId: z
      .string()
      .optional()
      .describe("ALWAYS pass the Restaurant ID from [SYSTEM: PERSISTENT CONTEXT]."),
  });

  private getCollectionName(restaurantId: string): string {
    return `kb-${restaurantId}`;
  }

  async execute(input: z.infer<typeof this.inputSchema>): Promise<any> {
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
      return noContextError({ hint: "Restaurant ID needed for knowledge base." });
    }

    const collection = this.getCollectionName(restaurantId);

    switch (input.action) {
      case "search": {
        if (!input.query) {
          return validationError("A search query is required.");
        }

        try {
          const searchQuery = input.category
            ? `${input.query} category:${input.category}`
            : input.query;

          const results = await Data.search(collection, searchQuery, 5, 0.65);

          if (results.length === 0) {
            return {
              status: "success",
              message: "No matching knowledge found.",
              results: [],
              miya_directive:
                "Tell the user you couldn't find relevant procedures/info in their language. If appropriate, offer to add this knowledge to the base for future reference.",
            };
          }

          return {
            status: "success",
            message: `Found ${results.length} relevant knowledge entry(ies).`,
            results: results.map((entry) => ({
              id: entry.id,
              title: entry.title,
              category: entry.category,
              content: entry.content,
              relevance: Math.round((entry.score || 0) * 100),
            })),
            miya_directive:
              "Present the most relevant result clearly in the user's language. Include the procedure steps if it's an SOP. Cite the title so the user knows which document you're referencing.",
          };
        } catch {
          return {
            status: "success",
            message: "Knowledge base is empty — no documents stored yet.",
            results: [],
            miya_directive:
              "Let the user know the knowledge base hasn't been set up yet. Offer to help them add SOPs, menus, or policies.",
          };
        }
      }

      case "add": {
        if (!input.title || !input.content) {
          return validationError("Both title and content are required to add knowledge.");
        }

        const category = input.category || "general";
        const searchText = [
          input.title,
          category,
          input.content,
          restaurantId,
        ].join(" ");

        const entry = await Data.create(
          collection,
          {
            title: input.title,
            category,
            content: input.content,
            restaurantId,
            addedBy: (user as any).uid || "manager",
            addedAt: new Date().toISOString(),
          },
          searchText
        );

        return {
          status: "success",
          message: `Knowledge entry "${input.title}" added to ${category}.`,
          entry_id: entry.id,
          miya_directive:
            "Confirm to the user in their language that the procedure/policy has been saved. They and their staff can now ask about it and Miya will find it.",
        };
      }

      case "update": {
        if (!input.entry_id) {
          return validationError("Entry ID is required for updates.");
        }

        const updateData: Record<string, unknown> = {};
        if (input.title) updateData.title = input.title;
        if (input.content) updateData.content = input.content;
        if (input.category) updateData.category = input.category;
        updateData.updatedAt = new Date().toISOString();

        const newSearchText = [
          input.title || "",
          input.category || "",
          input.content || "",
          restaurantId,
        ]
          .filter(Boolean)
          .join(" ");

        await Data.update(
          collection,
          input.entry_id,
          updateData,
          newSearchText || undefined
        );

        return {
          status: "success",
          message: "Knowledge entry updated.",
          miya_directive:
            "Confirm the update to the user in their language.",
        };
      }

      case "list": {
        try {
          const filter = input.category
            ? { category: input.category }
            : {};
          const page = await Data.get(collection, filter, 1, 20);

          if (!page.data || page.data.length === 0) {
            return {
              status: "success",
              message: input.category
                ? `No knowledge entries found in "${input.category}".`
                : "Knowledge base is empty.",
              entries: [],
            };
          }

          return {
            status: "success",
            message: `Found ${page.pagination.totalCount} knowledge entry(ies).`,
            entries: page.data.map((e: any) => ({
              id: e.id,
              title: e.data?.title,
              category: e.data?.category,
              addedAt: e.data?.addedAt,
            })),
            pagination: page.pagination,
            miya_directive:
              "List the knowledge entries by title and category in the user's language. Offer to show the full content of any specific entry.",
          };
        } catch {
          return {
            status: "success",
            message: "Knowledge base is empty.",
            entries: [],
          };
        }
      }
    }
  }
}
