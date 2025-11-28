import { PreProcessor } from "lua-cli";
const messageMatchingPreProcessor = new PreProcessor({
    name: "message-matching",
    description: "Matches the message to the appropriate skill",
    context: "Matches the message to the appropriate skill",
    execute: async (user, messages, channel) => {
        console.log("Message matching pre processor", user, messages, channel);
        console.log("User data", await user.data);
        console.log("Messages", messages);
        console.log("Channel", channel);
        //check if message type text contains test and return the message
        const testMessage = messages.find((message) => message.type === "text" && message.text.includes("test"));
        if (testMessage) {
            return [{ type: "text", text: "Tell the user that you got their test message and nothing else" }];
        }
        return messages;
    }
});
export default messageMatchingPreProcessor;
