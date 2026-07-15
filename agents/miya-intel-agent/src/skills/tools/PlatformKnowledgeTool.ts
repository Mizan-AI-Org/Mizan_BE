/**
 * Platform feature / workflow help (not tenant SOPs).
 * Tenant procedures stay on knowledge_base (Lua Data).
 */
import { LuaTool, User } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";
import { noContextError } from "./_common/errors";

const _api = new ApiService();

export default class PlatformKnowledgeTool implements LuaTool {
  name = "platform_knowledge";
  description =
    "Search Mizan/Miya platform help: how features work (sales, stock, food cost, invoices/PO match, " +
    "staff companion, digests, RBAC). Use when someone asks 'how do I…' about the product itself, " +
    "not restaurant-specific SOPs (those use knowledge_base).";

  inputSchema = z.object({
    query: z.string().describe("Natural language question about Mizan/Miya features or workflows."),
    audience: z
      .enum(["manager", "staff"])
      .optional()
      .describe("Filter guides by audience when known."),
    limit: z.number().int().positive().max(20).optional().default(5),
  });

  async execute(input: z.infer<typeof this.inputSchema>): Promise<any> {
    const user = await User.get();
    if (!user) return noContextError();

    const data = await _api.searchPlatformKnowledge(input.query, {
      limit: input.limit,
      audience: input.audience,
    });

    if (data?.success === false && !data?.results?.length) {
      return {
        status: "error",
        message: data?.error || "Couldn't search platform knowledge.",
      };
    }

    const results = data?.results || [];
    return {
      status: "success",
      message: data?.message_for_user || `Found ${results.length} guide(s).`,
      results,
      miya_directive:
        data?.miya_directive ||
        "Answer from these guides. For live restaurant numbers call the relevant data tools. " +
          "For tenant SOPs use knowledge_base.",
    };
  }
}
