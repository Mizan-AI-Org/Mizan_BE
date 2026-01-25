
import { OpenAI } from "@langchain/openai";
import { PromptTemplate } from "@langchain/core/prompts";
import { z } from "zod";

export class IncidentManagementModule {
    private model: OpenAI;

    constructor() {
        this.model = new OpenAI({ temperature: 0, modelName: "gpt-4" });
    }

    async analyzeIncident(description: string, metadata: any = {}) {
        /*
         * Analyze the incident description to determine:
         * - Summary (short title)
         * - Category (Safety, Maintenance, HR, Service, General)
         * - Priority (LOW, MEDIUM, HIGH, CRITICAL)
         * - Suggested Action
         */

        const prompt = PromptTemplate.fromTemplate(`
            You are an AI assistant for restaurant management.
            Analyze the following incident report and extract key details.
            
            Incident Description: "{description}"
            Reporter Context: {context}
            
            Return a JSON object with the following fields:
            - summary: A concise title for the incident (max 50 chars).
            - category: One of [Safety, Maintenance, HR, Service, General].
            - priority: One of [LOW, MEDIUM, HIGH, CRITICAL].
            - suggestedAction: A brief recommendation for the manager.
            
            JSON Response:
            `);

        const chain = prompt.pipe(this.model);

        try {
            const contextStr = JSON.stringify(metadata);
            const res = await chain.invoke({ description, context: contextStr });
            const jsonStr = (res as string).trim().replace(/```json/g, '').replace(/```/g, '');
            const analysis = JSON.parse(jsonStr);

            return {
                analysis,
                timestamp: new Date().toISOString()
            };
        } catch (error) {
            console.error("Error analyzing incident:", error);
            // Fallback
            return {
                analysis: {
                    summary: "New Incident",
                    category: "General",
                    priority: "MEDIUM",
                    suggestedAction: "Review details."
                },
                timestamp: new Date().toISOString()
            };
        }
    }
}
