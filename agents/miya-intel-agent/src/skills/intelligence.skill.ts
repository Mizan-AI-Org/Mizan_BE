import { LuaSkill } from "lua-cli";
import EventHistoryTool from "./tools/EventHistoryTool";
import OperationalKnowledgeTool from "./tools/OperationalKnowledgeTool";
import PlatformKnowledgeTool from "./tools/PlatformKnowledgeTool";
import { SummarizeContentTool, SentimentAnalysisTool, SmartReportTool } from "./tools/AIAnalysisTool";
import DemandForecastTool from "./tools/DemandForecastTool";
import ProactiveInsightsTool from "./tools/ProactiveInsightsTool";

export const intelligenceSkill = new LuaSkill({
  name: "intelligence",
  description:
    "AI analysis, knowledge base management, platform feature help, semantic event history search, " +
    "demand forecasting, and proactive operational insights.",
  context:
    "Handles intelligence operations: semantic event history search, knowledge base " +
    "management (SOPs, menus, policies), platform_knowledge for product/feature help, " +
    "content summarization, sentiment analysis, smart report generation, AI-powered demand " +
    "forecasting, and proactive insights.",
  tools: [
    new EventHistoryTool(),
    new OperationalKnowledgeTool(),
    new PlatformKnowledgeTool(),
    new SummarizeContentTool(),
    new SentimentAnalysisTool(),
    new SmartReportTool(),
    new DemandForecastTool(),
    new ProactiveInsightsTool(),
  ],
});
