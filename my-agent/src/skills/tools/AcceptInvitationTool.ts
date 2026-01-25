import { LuaTool, env } from "lua-cli";
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

    async execute(input: z.infer<typeof this.inputSchema>, context?: any) {
        // Token retrieval with service account fallback
        const token =
            context?.metadata?.token ||
            (context?.get ? context.get("token") : undefined) ||
            context?.user?.data?.token ||
            context?.user?.token ||
            env('MIZAN_SERVICE_TOKEN');

        if (!token) {
            console.error('[AcceptInvitationTool] No authentication token available.');
            return {
                status: "error",
                message: "I can't process your invitation right now. Please try again later."
            };
        }

        console.log(`[AcceptInvitationTool] Looking up invitation for ${input.phone}`);

        try {
            // 1. Lookup invitation
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
                pin: "0000" // Automatic default PIN as requested
            }, token);

            if (acceptResult.success) {
                console.log(`[AcceptInvitationTool] ✅ Invitation accepted for ${acceptResult.user.email}`);
                return {
                    status: "success",
                    message: `🎉 *You're in!*\n\nWelcome to *${invitation.restaurant_name}*, ${acceptResult.user.first_name}.\n\nYour account has been created successfully. Your default PIN is *0000*. You can use this PIN to clock in/out on the system.\n\nHow can I help you today?`,
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
