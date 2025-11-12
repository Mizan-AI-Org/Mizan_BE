import { LuaSkill } from "lua-cli";
import { CreateBasketTool, GetBasketByIdTool, UpdateBasketStatusTool, UpdateBasketMetadataTool, CheckoutBasketTool, GetBasketsTool, ClearBasketTool, RemoveItemFromBasketTool, AddItemToBasketTool } from "./tools/BasketTool";

const basketSkill = new LuaSkill({
    name: "basket-skill",
    version: "1.0.0",
    description: "Basket management skill",
    context: "Basket management skill",
    tools: [new CreateBasketTool(), new GetBasketByIdTool(), new UpdateBasketStatusTool(), new UpdateBasketMetadataTool(), new CheckoutBasketTool(), new GetBasketsTool(), new AddItemToBasketTool(), new RemoveItemFromBasketTool(), new ClearBasketTool()],
});

export default basketSkill;