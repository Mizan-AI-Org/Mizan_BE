import { LuaSkill } from "lua-cli";
import { SearchProductsTool, CreateProductTool, UpdateProductTool, GetAllProductsTool, GetProductByIdTool, DeleteProductTool } from "./tools/ProductsTool";


const productSkill = new LuaSkill({
    name: "product-skill",
    version: "1.0.0",
    description: "Product management skill",
    context: "Product management skill",
    tools: [new SearchProductsTool(), new CreateProductTool(), new UpdateProductTool(), new GetAllProductsTool(), new GetProductByIdTool(), new DeleteProductTool()],
});

export default productSkill;