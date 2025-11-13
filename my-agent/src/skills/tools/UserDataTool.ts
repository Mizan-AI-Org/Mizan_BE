import { AI, LuaTool, User, Jobs, JobInstance } from "lua-cli";
import { z } from "zod";


export class GetUserDataTool implements LuaTool {
    name = "get_user_data";
    description = "Get the user data for a given user id";
    inputSchema = z.object({ });

    constructor() {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        return {
            success: true,
            message: "User data retrieved",
            data: user.data,
            name: user.name,
            age: user.age
        };
    }
}

export class UpdateUserDataTool implements LuaTool {
    name = "update_user_data";
    description = "Update the user data for a given user id";
    inputSchema = z.object({ 
        data: z.object({
            name: z.string().optional(),
            age: z.number().optional()
        })
    });

    constructor() {}

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get(); //get instance of user
        user.name = input.data.name;
        user.age = input.data.age;
        await user.save();
        // await user.send([{ type: "text", text: "Hello, how are you?" }]);
        return {
            success: true,
            message: "User data updated"
        };
    }
}

export class WritePoemTool implements LuaTool {
    name = "write_poem";
    description = "Write a poem about a given topic";
    inputSchema = z.object({
        topic: z.string()
    });
    async execute(input: z.infer<typeof this.inputSchema>) {
        return await AI.generate("Write a poem about the following topic:", [{ type: "text", text: input.topic }]);
    }
}

export class CreateInlineJobTool implements LuaTool {
    name = "create-job-to-notify-user";
    description = "Create a new job to notify the user";
    inputSchema = z.object({
        message: z.string()
    });
    async execute(input: z.infer<typeof this.inputSchema>) {
        //create a job to notify the user in exactly 20 seconds
        const job = await Jobs.create({
            name: "notify-user-new",
            description: "Notify the user",
            schedule: {
                type: "once",
                executeAt: new Date(Date.now() + 0)
            },
            metadata: {
                message: input.message
            },
            execute: async (jobInstance: JobInstance) => {
                const user = await jobInstance.user();
                const metadata = jobInstance.metadata; //we do this to access runtime variables in the job when the job is executed
                console.log("Metadata", metadata);
                await user.send([{ type: "text", text: metadata.message }]);
                return { success: true, message: "User notified" };
            }
        });
        return { success: true, job: job };
    }
}