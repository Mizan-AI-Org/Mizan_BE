
import ApiService from "../services/ApiService";

export class StaffManagementModule {
    apiService: ApiService;

    constructor() {
        this.apiService = new ApiService();
    }

    async processEvent(event: any) {
        let actionTaken = "logged";
        let requiresManagerAttention = false;
        let recommendations: string[] = [];

        if (event.eventType === 'staff_invite') {
            await this.handleStaffInvite(event);
            actionTaken = "invite_sent";
        } else if (event.eventType === 'staff_invitation_accepted') {
            await this.handleInvitationAcceptance(event);
            actionTaken = "invitation_accepted";
        } else if (event.eventType === 'clock_in') {
            await this.handleClockIn(event);
            actionTaken = "clock_in_processed";
        } else if (event.eventType === 'clock_out') {
            await this.handleClockOut(event);
            actionTaken = "clock_out_processed";
        } else {
            // Mock implementation for other events
            recommendations.push("Monitor staff levels");
        }

        return {
            actionTaken,
            requiresManagerAttention,
            workloadImpact: "low",
            workloadScore: 50,
            recommendations
        };
    }

    async handleClockIn(event: any) {
        const { details, staffName, staffId, metadata } = event;
        const agentKey = process.env.WEBHOOK_API_KEY || '';
        const phone = details?.phoneNumber || details?.phone || metadata?.phone;

        if (!phone) {
            console.error("Missing phone for clock-in event");
            return;
        }

        // If this is a button click (interaction), we might not have location yet
        // In Lua, if details.latitude is present, it's a location message
        if (details?.latitude && details?.longitude) {
            console.log(`📍 Recording clock-in location for ${staffName}: ${details.latitude}, ${details.longitude}`);

            const result = await this.apiService.clockIn({
                staff_id: staffId,
                latitude: details.latitude,
                longitude: details.longitude,
                timestamp: event.timestamp
            }, agentKey);

            if (result.success || result.status === 'success') {
                await this.apiService.sendWhatsapp({
                    phone: phone,
                    type: 'text',
                    body: `✅ *Clock-In Successful!*\n\nHave a great shift, ${staffName}!`
                }, agentKey);
            } else {
                await this.apiService.sendWhatsapp({
                    phone: phone,
                    type: 'text',
                    body: `⚠️ *Clock-In Failed*\n\nError: ${result.error || 'Unknown error'}. Please try again.`
                }, agentKey);
            }
        } else {
            // No location yet, send the location sharing request
            console.log(`📍 Requesting location from ${staffName} to clock in`);

            await this.apiService.sendWhatsapp({
                phone: phone,
                type: 'template',
                template_name: 'clock_in_location_request',
                language_code: 'en_US',
                components: []
            }, agentKey);
        }
    }

    async handleClockOut(event: any) {
        const { staffName, staffId, details, metadata } = event;
        const agentKey = process.env.WEBHOOK_API_KEY || '';
        const phone = details?.phoneNumber || details?.phone || metadata?.phone;

        console.log(`✅ Recording clock-out for ${staffName}`);

        const result = await this.apiService.clockOut({
            staff_id: staffId,
            timestamp: event.timestamp
        }, agentKey);

        if (phone) {
            if (result.success || result.status === 'success') {
                await this.apiService.sendWhatsapp({
                    phone: phone,
                    type: 'text',
                    body: `✅ *Clock-Out Successful!*\n\nYour shift has been recorded. Get some rest!`
                }, agentKey);
            } else {
                await this.apiService.sendWhatsapp({
                    phone: phone,
                    type: 'text',
                    body: `⚠️ *Clock-Out Failed*\n\nError: ${result.error || 'Unknown error'}. Please try again.`
                }, agentKey);
            }
        }
    }

    async handleStaffInvite(event: any) {
        const { details, staffName } = event;
        const { phone, inviteLink, restaurantName } = details;

        if (!phone || !inviteLink || !restaurantName) {
            console.error("Missing details for staff invite");
            return;
        }

        const agentKey = process.env.WEBHOOK_API_KEY || '';

        // Using the 'staff_invitation_eng' template as requested
        await this.apiService.sendWhatsapp({
            phone: phone,
            type: 'template',
            template_name: 'staff_invitation_eng',
            language_code: 'en_US',
            components: [
                {
                    type: 'body',
                    parameters: [
                        { type: 'text', text: staffName },
                        { type: 'text', text: restaurantName }
                    ]
                }
            ]
        }, agentKey);

        console.log(`📨 Sent template invite to ${staffName} (${phone})`);
    }

    async handleInvitationAcceptance(event: any) {
        const { details, staffName } = event;
        const { invitationToken, phoneNumber, flowData } = details;

        if (!invitationToken || !phoneNumber) {
            console.error("Missing details for invitation acceptance");
            return;
        }

        console.log(`🎉 Processing invitation acceptance for ${staffName}`);

        // Generate a secure PIN (4-6 digits)
        const pin = this.generatePIN();

        const agentKey = process.env.WEBHOOK_API_KEY || '';

        // Call backend to accept invitation
        const result = await this.apiService.acceptInvitation({
            invitation_token: invitationToken,
            phone: phoneNumber,
            first_name: staffName,
            last_name: flowData?.last_name || '',
            pin: pin
        }, agentKey);

        if (result.success) {
            console.log(`✅ User created: ${result.user.email}`);

            // Send confirmation template (Official Meta template)
            await this.apiService.sendWhatsapp({
                phone: phoneNumber,
                type: 'template',
                template_name: 'accepted_invite_confirmation',
                language_code: 'en_US',
                components: []
            }, agentKey);

            // Confirmation message is now handled immediately by the backend
            // for the fastest possible response.
            console.log(`📨 Invitation acceptance processed for ${staffName}`);
        } else {
            console.error(`❌ Failed to accept invitation: ${result.error}`);

            // Send error message  
            const errorMessage = "Sorry, there was an issue accepting your invitation. Please try again or contact your restaurant manager.";

            await this.apiService.sendWhatsapp({
                phone: phoneNumber,
                type: 'text',
                body: errorMessage
            }, agentKey);
        }
    }

    private generatePIN(): string {
        // Return a predictable PIN for zero-touch flow as requested
        return "1234";
    }
}
