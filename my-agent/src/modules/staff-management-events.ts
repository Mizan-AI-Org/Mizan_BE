
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

    async handleStaffInvite(event: any) {
        const { details, staffName } = event;
        const { phone, inviteLink, restaurantName } = details;

        if (!phone || !inviteLink || !restaurantName) {
            console.error("Missing details for staff invite");
            return;
        }

        const agentKey = process.env.WEBHOOK_API_KEY || '';

        // Using the 'staff_invitation' template seen in dashboard
        await this.apiService.sendWhatsapp({
            phone: phone,
            type: 'template',
            template_name: 'staff_invitation',
            language_code: 'en_US',
            components: [
                {
                    type: 'body',
                    parameters: [
                        { type: 'text', text: staffName },
                        { type: 'text', text: restaurantName }
                    ]
                },
                {
                    type: 'button',
                    sub_type: 'url',
                    index: '0',
                    parameters: [
                        { type: 'text', text: inviteLink.split('token=')[1] } // Assuming button URL uses token as suffix
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

            // Send confirmation message
            const confirmationMessage = `🎉 *You're in!*\n\nYour invitation has been accepted successfully.\n\n*Your PIN:* ${pin}\n\nYou can now use this PIN to clock in/out and access your schedule.\n\nThanks!`;

            await this.apiService.sendWhatsapp({
                phone: phoneNumber,
                type: 'text',
                body: confirmationMessage
            }, agentKey);

            console.log(`📨 Sent confirmation to ${staffName} (${phoneNumber})`);
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
        // Generate a random 4-digit PIN
        return Math.floor(1000 + Math.random() * 9000).toString();
    }
}
