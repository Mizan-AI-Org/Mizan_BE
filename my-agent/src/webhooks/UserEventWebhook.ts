/**
 * User Event Webhook Example
 * 
 * This webhook receives user events from external systems (e.g., CRM, marketing tools).
 * It validates incoming data and stores events for processing.
 * 
 * Webhook URL: {API_URL}/webhooks/{agentId}/{webhookId}
 */

import { LuaWebhook, Data } from "lua-cli";
import { z } from "zod";

const userEventWebhook = new LuaWebhook({
  // Use a distinct name to avoid collision with an existing webhook on the server
  name: "user-events-dev",
  version: "1.0.0",
  description: "Receives user events from external systems",
  context: "This webhook handles user registration, profile updates, and deletion events. " +
           "It validates the incoming data, stores events in the database, and can trigger follow-up actions.",

  // Validate query parameters (optional source tracking)
  querySchema: z.object({
    source: z.string().optional(),
    version: z.string().optional()
  }),

  // Validate headers (require API key for security)
  headerSchema: z.object({
    'x-api-key': z.string(),
    'content-type': z.string().optional()
  }),

  // Validate request body
  bodySchema: z.object({
    eventType: z.enum(['signup', 'update', 'delete']),
    userId: z.string(),
    email: z.string().email(),
    name: z.string().optional(),
    metadata: z.record(z.any()).optional(),
    timestamp: z.string()
  }),

  execute: async ({ query, headers, body }) => {
    console.log(`📥 Received ${body.eventType} event for user:`, body.email);
    console.log(`📍 Source:`, query?.source || 'unknown');

    // Security: Validate API key (in production, use env variable)
    const expectedKey = process.env.WEBHOOK_API_KEY || 'your-secret-key';
    if (headers['x-api-key'] !== expectedKey) {
      throw new Error('Invalid API key');
    }

    // Store the event in custom data collection
    const eventData = {
      ...body,
      source: query?.source,
      receivedAt: new Date().toISOString(),
      processed: false
    };

    const result = await Data.create('user-events', eventData, 
      `${body.eventType} ${body.email} ${body.name || ''}`
    );

    console.log('✅ Event stored successfully:', result.id);

    // Return success response
    return {
      success: true,
      eventId: result.id,
      userId: body.userId,
      timestamp: new Date().toISOString()
    };
  }
});

export default userEventWebhook;

