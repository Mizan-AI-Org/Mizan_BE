import { LuaSkill } from "lua-cli";
import { CreateInlineJobTool, GetUserDataTool, UpdateUserDataTool, WritePoemTool } from "../skills/tools/UserDataTool";


const userSkill = new LuaSkill({
    name: "user-skill",
    version: "1.0.0",
    description: "User management skill",
    context: "User management skill",
    tools: [new GetUserDataTool(), new UpdateUserDataTool(), new WritePoemTool(), new CreateInlineJobTool()],
});

export default userSkill;