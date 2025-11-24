import { LuaSkill } from "lua-cli";
import InventoryTool from "./tools/InventoryTool";

export const restaurantOpsSkill = new LuaSkill({
  name: "restaurant-ops",
  description: "Guest experience, inventory, and kitchen coordination",
  context: "Manage inventory, waste tracking, baskets, orders, and payments",
  tools: [
    new InventoryTool(),
  ],
});