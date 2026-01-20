import { ChatMessage, PreProcessor, UserDataInstance } from "lua-cli";


const messageMatchingPreProcessor = new PreProcessor({
    name: "message-matching",
    description: "Matches the message to the appropriate skill",
    execute: async (user: UserDataInstance, messages: ChatMessage[], channel: string) => {
        console.log("Message matching pre processor", user, messages, channel);
        console.log("User data", await user.data);
        console.log("Messages", messages);
        console.log("Channel", channel);
        //check if message type text contains test and return the message
        const testMessage = messages.find((message) => message.type === "text" && message.text.includes("test"));
        if (testMessage) {
            return { action: "proceed", modifiedMessage: [{ type: "text", text: "Tell the user that you got their test message and nothing else" }] };
        }
        return { action: "proceed" };
    }
});

export default messageMatchingPreProcessor;