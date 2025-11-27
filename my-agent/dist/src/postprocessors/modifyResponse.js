import { PostProcessor } from "lua-cli";
const modifyResponsePostProcessor = new PostProcessor({
    name: "modify-response",
    description: "Modifies the response to the user",
    context: "Modifies the response to the user",
    execute: async (user, message, response, channel) => {
        console.log("Modify response post processor", user, message, response, channel);
        console.log("User data", await user.data);
        console.log("Message", message);
        console.log("Response", response);
        console.log("Channel", channel);
        if (response.includes("test")) {
            return message.toUpperCase();
        }
        return response;
    }
});
export default modifyResponsePostProcessor;
