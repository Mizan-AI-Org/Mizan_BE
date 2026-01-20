import { LuaSkill } from "lua-cli";
import RestaurantLookupTool from "./tools/RestaurantLookupTool";
import StaffLookupTool from "./tools/StaffLookupTool";
import BusinessContextTool from "./tools/BusinessContextTool";
import IncidentReportTool from "./tools/IncidentReportTool";

export const restaurantOpsSkill = new LuaSkill({
  name: "restaurant-operations",
  description: "Restaurant-specific operations and context management",
  context: "Manage restaurant data, retrieve context, and handle operational queries",
  tools: [
    new RestaurantLookupTool(),
    new StaffLookupTool(),
    new BusinessContextTool(),
    new IncidentReportTool()
  ]
});