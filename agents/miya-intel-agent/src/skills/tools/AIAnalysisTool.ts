/**
 * AI-powered analysis tools using Lua's AI.generate() API.
 * Provides content summarization, sentiment analysis, and
 * smart content generation — all running as sub-LLM calls
 * within tool execution.
 */
import { LuaTool, AI, User } from "lua-cli";
import { z } from "zod";
import { noContextError } from "./_common/errors";

export class SummarizeContentTool implements LuaTool {
  name = "summarize_content";
  description =
    "Summarize long text (staff feedback threads, reports, incident logs, " +
    "conversation histories, documents). Use when the manager asks 'summarize this', " +
    "'give me the highlights', 'what's the gist?', or when presenting a long tool result.";

  inputSchema = z.object({
    content: z.string().describe("The text to summarize"),
    style: z
      .enum(["brief", "detailed", "bullet_points"])
      .default("brief")
      .describe("brief: 1-2 sentences. detailed: full paragraph. bullet_points: key points list."),
    language: z
      .string()
      .optional()
      .describe("Target language for summary (e.g. 'French', 'Arabic'). Defaults to matching the content language."),
  });

  async execute(input: z.infer<typeof this.inputSchema>) {
    const styleInstructions: Record<string, string> = {
      brief: "Summarize in 1-2 concise sentences.",
      detailed: "Provide a thorough summary in one paragraph covering all key points.",
      bullet_points: "List the key points as bullet points (max 7 bullets).",
    };

    const langNote = input.language
      ? `Respond in ${input.language}.`
      : "Respond in the same language as the content.";

    try {
      const summary = await AI.generate(
        `You are a professional operations summarizer for a restaurant/business. ${styleInstructions[input.style]} ${langNote} Be factual — never invent details.`,
        [{ type: "text", text: input.content }]
      );

      return {
        status: "success",
        summary: typeof summary === "string" ? summary : (summary as any).text || String(summary),
        style: input.style,
        miya_directive:
          "Present this summary directly to the user in their language. Do not add a preamble — the summary IS the response.",
      };
    } catch (error) {
      return {
        status: "error",
        message: "Summarization temporarily unavailable.",
        miya_directive:
          "Apologize briefly in the user's language and offer to show the full content instead.",
      };
    }
  }
}

export class SentimentAnalysisTool implements LuaTool {
  name = "analyze_sentiment";
  description =
    "Analyze the sentiment/mood of staff messages, feedback, or conversation threads. " +
    "Use when the manager asks 'how is the team feeling?', 'is staff morale okay?', " +
    "or to gauge the tone of a complaint/request before responding.";

  inputSchema = z.object({
    messages: z
      .array(
        z.object({
          from: z.string().optional().describe("Who sent it (name or role)"),
          text: z.string().describe("The message content"),
        })
      )
      .describe("Array of messages to analyze"),
    context: z
      .string()
      .optional()
      .describe("Additional context (e.g. 'these are staff messages from this morning')"),
  });

  async execute(input: z.infer<typeof this.inputSchema>) {
    const messagesText = input.messages
      .map(
        (m, i) =>
          `[${i + 1}] ${m.from ? `${m.from}: ` : ""}${m.text}`
      )
      .join("\n");

    try {
      const systemPrompt =
        "You are an expert at analyzing workplace sentiment in restaurants and hospitality. " +
        "Analyze the messages and return a JSON object with: " +
        "overall_sentiment (positive/neutral/negative/mixed), " +
        "confidence (0-1), " +
        "summary (1 sentence description of the mood), " +
        "concerns (array of specific concerns if negative/mixed), " +
        "positives (array of positive signals if any). " +
        "Return ONLY valid JSON, no markdown.";

      const text = await AI.generate(systemPrompt, [
        {
          type: "text",
          text: `${input.context ? `Context: ${input.context}\n\n` : ""}Messages:\n${messagesText}`,
        },
      ]);

      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(text.replace(/```json?\n?/g, "").replace(/```/g, "").trim());
      } catch {
        parsed = { overall_sentiment: "unknown", summary: text };
      }

      return {
        status: "success",
        analysis: parsed,
        message_count: input.messages.length,
        miya_directive:
          "Present the sentiment analysis to the manager in their language. If concerns are found, suggest concrete actions (e.g. 'You might want to check in with the kitchen team — they seem stressed'). Never show raw JSON.",
      };
    } catch {
      return {
        status: "error",
        message: "Sentiment analysis temporarily unavailable.",
        miya_directive:
          "Apologize briefly and offer to review the messages manually.",
      };
    }
  }
}

export class SmartReportTool implements LuaTool {
  name = "generate_smart_report";
  description =
    "Generate an AI-powered narrative report from structured data. Use when presenting " +
    "sales summaries, attendance reports, or operational data — turns raw numbers into " +
    "actionable insights with recommendations.";

  inputSchema = z.object({
    report_type: z
      .enum(["sales", "attendance", "operations", "staffing", "custom"])
      .describe("Type of report to generate"),
    data: z
      .record(z.unknown())
      .describe("The structured data to analyze (e.g. sales figures, attendance records)"),
    period: z
      .string()
      .optional()
      .describe("Time period covered (e.g. 'today', 'this week', 'March 2026')"),
    language: z
      .string()
      .optional()
      .describe("Target language. Defaults to the user's conversation language."),
  });

  async execute(input: z.infer<typeof this.inputSchema>) {
    const langNote = input.language
      ? `Write the report in ${input.language}.`
      : "Write the report in English.";

    const reportPrompts: Record<string, string> = {
      sales:
        "Analyze the sales data. Highlight top performers, trends, and areas of concern. Suggest actions to improve revenue.",
      attendance:
        "Analyze attendance data. Flag chronic lateness, no-shows, and exemplary attendance. Suggest staffing adjustments.",
      operations:
        "Analyze operational data. Identify bottlenecks, efficiency gains, and areas needing attention.",
      staffing:
        "Analyze staffing data. Flag understaffing/overstaffing, overtime risks, and scheduling opportunities.",
      custom: "Analyze the data provided and generate actionable insights.",
    };

    try {
      const text = await AI.generate(
        `You are an experienced restaurant operations analyst. ${reportPrompts[input.report_type]} ${langNote}\n\nFormat: Start with a 1-line headline summary, then 3-5 key findings with specific numbers, then 2-3 concrete recommendations. Keep it under 200 words. Be specific — reference actual data points.`,
        [
          {
            type: "text",
            text: `Report period: ${input.period || "current"}\nData:\n${JSON.stringify(input.data, null, 2)}`,
          },
        ]
      );

      return {
        status: "success",
        report: text,
        report_type: input.report_type,
        period: input.period,
        miya_directive:
          "Present this report directly to the manager in their language. The report IS the response — do not add commentary unless asked.",
      };
    } catch {
      return {
        status: "error",
        message: "Report generation temporarily unavailable.",
        miya_directive:
          "Apologize and offer to present the raw data instead.",
      };
    }
  }
}
