"use strict";
`` `
import { LuaSkill } from "lua-cli";
import RestaurantLookupTool from "./tools/RestaurantLookupTool";

export const restaurantOpsSkill = new LuaSkill({
  name: "restaurant-operations",
  description: "Restaurant-specific operations and context management",
  context: "Manage restaurant data, retrieve context, and handle operational queries",
  tools: [
    new RestaurantLookupTool()
  ]
});
` ``;
