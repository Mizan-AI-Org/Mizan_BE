import { LuaSkill } from "lua-cli";
import EventHistoryTool from "./tools/EventHistoryTool";
import OperationalKnowledgeTool from "./tools/OperationalKnowledgeTool";
import { SummarizeContentTool, SentimentAnalysisTool, SmartReportTool } from "./tools/AIAnalysisTool";
import DemandForecastTool from "./tools/DemandForecastTool";
import ProactiveInsightsTool from "./tools/ProactiveInsightsTool";

export const intelligenceSkill = new LuaSkill({
  name: "intelligence",
  description:
    "AI analysis, knowledge base management, semantic event history search, " +
    "demand forecasting, and proactive operational insights.",
  context:
    "Handles intelligence operations: semantic event history search, knowledge base " +
    "management (SOPs, menus, policies), content summarization, sentiment analysis, " +
    "smart report generation, AI-powered demand forecasting, and proactive insights.",
  tools: [
    new EventHistoryTool(),
    new OperationalKnowledgeTool(),
    new SummarizeContentTool(),
    new SentimentAnalysisTool(),
    new SmartReportTool(),
    new DemandForecastTool(),
    new ProactiveInsightsTool(),
  ],
});
