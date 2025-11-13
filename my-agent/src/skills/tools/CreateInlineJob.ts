import { LuaTool } from "lua-cli";
import { z } from "zod";
import { AI, JobInstance, Jobs } from "../../../dist/api-exports";
import ApiService from "../services/ApiService";
import { v4 as uuidv4 } from 'uuid';

export default class CreateInlineJobTool implements LuaTool {
    name = "create_inline_job";
    description = "Create a new inline job";
    inputSchema = z.object({});

    async execute(input: z.infer<typeof this.inputSchema>) {
        const jobId = uuidv4();
        const job = await Jobs.create({
            name: `inline-job-${jobId}`,
            description: "Inline job",
            schedule: {
                type: "once",
                executeAt: new Date(Date.now() + 1000)
            },
            metadata: {
                test: "test"
            },
            execute: async (job: JobInstance) => {
                // console.log("Executing inline job", job);
                console.log("Inline job metadata", job.metadata);
                console.log("Inline job user", job.user());
                console.log("Inline job data", job.data);
                const response = await AI.generate("You are a poet. Write a poem about the following topic:", [{ type: "text", text: "A sheep in the field" }]);
                console.log("AI response", response);
                const apiService = new ApiService();
                const data = await apiService.fetchUserData("123");
                await job.updateMetadata({ test: "test2" });
                const disabled = await job.delete();
                console.log("Inline job deleted", disabled);
                return { success: true, data: response, user: data };
            }
        });

        return { success: true, job: job };
    }
}
