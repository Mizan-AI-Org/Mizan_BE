import { LuaSkill } from "lua-cli";
import DemandForecastTool from "./tools/DemandForecastTool";

export const predictiveAnalystSkill = new LuaSkill({
  name: "predictive-analyst",
  description: "Sales forecasting and demand signals",
  context: "Use these tools to fetch weather, product signals, and generate demand forecasts",
  tools: [
    new DemandForecastTool(),
  ]
});