import { LuaTool, User, env, Templates } from "lua-cli";
import { z } from "zod";
import ApiService from "../../services/ApiService";

export default class AcceptInvitationTool implements LuaTool {
    name = "accept_invitation";
    description = "Automatically accept a staff invitation when a user responds to an invite. This tool will lookup any pending invitations for the user's phone number and create their account immediately.";

    inputSchema = z.object({
        phone: z.string().describe("The user's phone number as extracted from the context"),
        first_name: z.string().optional().describe("User's first name if mentioned"),
        last_name: z.string().optional().describe("User's last name if mentioned")
    });

    private apiService: ApiService;

    constructor(apiService?: ApiService) {
        this.apiService = apiService || new ApiService();
    }

    async execute(input: z.infer<typeof this.inputSchema>) {
        const user = await User.get();
        if (!user) {
            return {
                status: "error",
                message: "I can't access your account context right now. Please try again in a moment."
            };
        }
        const userData = (user as any).data || {};
        const profile = (user as any)._luaProfile || {};

        // Token retrieval with service account fallback
        // Token retrieval: Prioritize the shared WEBHOOK_API_KEY for agent-to-backend calls
        const token =
            env('WEBHOOK_API_KEY') ||
            user.token ||
            userData.token ||
            profile.token ||
            profile.accessToken ||
            profile.credentials?.accessToken ||
            env('MIZAN_SERVICE_TOKEN');

        if (!token) {
            console.error('[AcceptInvitationTool] No authentication token available.');
            return {
                status: "error",
                message: "I can't process your invitation right now. Please try again later."
            };
        }

        console.log(`[AcceptInvitationTool] Received request for phone: ${input.phone}`);
        console.log(`[AcceptInvitationTool] Input details:`, input);

        console.log(`[AcceptInvitationTool] Looking up invitation for ${input.phone}`);

        try {
            // 1. Lookup invitation
            const lookupUrl = `${this.apiService.baseUrl}/api/accounts/agent/lookup-invitation/`;
            console.log(`[AcceptInvitationTool] Calling lookup at: ${lookupUrl} for phone: ${input.phone}`);

            const lookupResult = await this.apiService.lookupInvitation(input.phone, token);

            if (!lookupResult.success || !lookupResult.invitation) {
                return {
                    status: "error",
                    message: lookupResult.error || "I couldn't find any pending invitations for this number. If you've already accepted, please try logging in."
                };
            }

            const invitation = lookupResult.invitation;
            console.log(`[AcceptInvitationTool] Found invitation for ${invitation.first_name} at ${invitation.restaurant_name}`);

            // 2. Accept invitation (automatic with default PIN)
            const acceptResult = await this.apiService.acceptInvitation({
                invitation_token: invitation.token,
                phone: input.phone,
                first_name: input.first_name || invitation.first_name,
                last_name: input.last_name || invitation.last_name || '',
                pin: "1234" // Automatic default PIN as requested
            }, token);

            if (acceptResult.success) {
                console.log(`[AcceptInvitationTool] ✅ Invitation accepted for ${acceptResult.user.email}`);

                // Send the accepted_invite_confirmation template via Lua Templates API
                const channelId = env('WHATSAPP_CHANNEL_ID');
                if (channelId) {
                    const cleanPhone = String(input.phone).replace(/[^0-9]/g, '');
                    console.log(`[AcceptInvitationTool] 📱 Sending confirmation template to: ${cleanPhone}`);

                    try {
                        // Fetch the accepted_invite_confirmation template
                        const listResult = await Templates.whatsapp.list(channelId, { search: 'accepted_invite_confirmation' });
                        const template = listResult.templates.find(
                            (t: any) => t.name === 'accepted_invite_confirmation' && t.status === 'APPROVED'
                        );

                        if (template) {
                            console.log(`[AcceptInvitationTool] 🚀 Sending template '${template.name}' (ID: ${template.id})`);

                            const sendResult = await Templates.whatsapp.send(
                                channelId,
                                template.id,
                                {
                                    phoneNumbers: [cleanPhone],
                                    values: {}
                                }
                            );

                            console.log(`[AcceptInvitationTool] ✅ Confirmation template sent:`, JSON.stringify(sendResult));
                        } else {
                            console.warn(`[AcceptInvitationTool] ⚠️ Template 'accepted_invite_confirmation' not found or not approved`);
                        }
                    } catch (templateError: any) {
                        console.error(`[AcceptInvitationTool] ⚠️ Failed to send confirmation template:`, templateError.message);
                        // Don't fail the whole operation - the invitation was accepted successfully
                    }
                } else {
                    console.warn(`[AcceptInvitationTool] ⚠️ WHATSAPP_CHANNEL_ID not configured - skipping confirmation template`);
                }

                return {
                    status: "success",
                    message: "The invitation has been accepted successfully and the user has been notified with the official confirmation template.",
                    user: acceptResult.user
                };
            } else {
                return {
                    status: "error",
                    message: acceptResult.error || "Something went wrong while accepting your invitation. Please contact your manager."
                };
            }

        } catch (error: any) {
            console.error(`[AcceptInvitationTool] ❌ Unexpected error:`, error.message);
            return {
                status: "error",
                message: "An unexpected error occurred. Please try again later."
            };
        }
    }
}
