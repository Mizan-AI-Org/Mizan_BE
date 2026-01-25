
import { LuaWebhook, Data, Templates, env } from "lua-cli";
import { z } from "zod";

// WhatsApp Channel ID for sending templates via Lua
const WHATSAPP_CHANNEL_ID = "930508853476059";

const userEventWebhook = new LuaWebhook({
    name: "user-events",
    description: "Receives user events from Mizan Backend",

    // Validate headers (require API key for security)
    headerSchema: z.object({
        'x-api-key': z.string(),
        'content-type': z.string().optional()
    }),

    // Validate request body
    bodySchema: z.object({
        eventType: z.string(),
        staffId: z.string().optional(),
        staffName: z.string().optional(),
        role: z.string().optional(),
        details: z.record(z.any()).optional(),
        timestamp: z.string().optional(),
        version: z.string().optional()
    }),

    execute: async (event: any) => {
        const { headers, body } = event;
        console.log(`📥 [V3.0.0] Received ${body?.eventType} event`);

        // Security: Validate API key
        const expectedKey = env('WEBHOOK_API_KEY');
        const receivedKey = headers?.['x-api-key'];

        if (!receivedKey) {
            throw new Error('Missing API key');
        }

        if (expectedKey && receivedKey !== expectedKey) {
            throw new Error('Unauthorized: Invalid API key');
        }

        // Store the event in Data collection
        const eventData = {
            ...body,
            receivedAt: new Date().toISOString(),
            processed: false
        };

        try {
            await Data.create('user-events', eventData,
                `${body?.eventType} ${body?.staffName || ''}`
            );
        } catch (e) {
            console.warn("Failed to store event in Data collection:", e);
        }

        // Handle staff_invite event - Send WhatsApp template via Lua Templates API
        if (body?.eventType === 'staff_invite') {
            const phone = body.details?.phone;
            const restaurantName = body.details?.restaurantName;
            const firstName = body.staffName || "Staff Member";

            if (phone) {
                console.log(`🚀 Sending WhatsApp invite to ${phone} via Lua Templates API`);

                // Normalize phone: remove all non-digit characters (no + or spaces)
                // Example: "+220 373 6808" -> "2203736808"
                const cleanPhone = String(phone).replace(/[^0-9]/g, '');
                console.log(`📱 Normalized phone: ${cleanPhone}`);

                try {
                    // Send template using Lua's native Templates.whatsapp.send()
                    const result = await Templates.whatsapp.send(
                        WHATSAPP_CHANNEL_ID,
                        'staff_invitation',  // Template name from Lua Admin
                        {
                            phoneNumbers: [cleanPhone],
                            values: {
                                body: {
                                    customer_name: firstName,
                                    restaurant_name: restaurantName || 'Mizan'
                                }
                            }
                        }
                    );

                    console.log("✅ WhatsApp template send result:", JSON.stringify(result));

                    // Check for errors
                    if (result.errors && result.errors.length > 0) {
                        console.error("❌ Template send errors:", result.errors);
                        return {
                            success: false,
                            errors: result.errors,
                            phone: cleanPhone
                        };
                    }

                    // Success
                    const messageId = result.results?.[0]?.messageId;
                    console.log(`✅ WhatsApp invite sent successfully! MessageID: ${messageId}`);

                    return {
                        success: true,
                        messageId: messageId,
                        phone: cleanPhone,
                        template: 'staff_invitation'
                    };

                } catch (error: any) {
                    console.error("❌ Failed to send WhatsApp template:", error.message);
                    return {
                        success: false,
                        error: error.message,
                        phone: cleanPhone
                    };
                }
            } else {
                console.warn("⚠️ No phone number provided for staff_invite event");
            }
        }

        return {
            success: true,
            processed: true
        };
    }
});

export default userEventWebhook;
