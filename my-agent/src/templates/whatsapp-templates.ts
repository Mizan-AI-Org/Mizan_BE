/**
 * WhatsApp Templates for Mizan AI
 * Programmatically creates templates using Lua API
 * https://docs.heylua.ai/api/templates
 */

import axios from 'axios';

interface LuaTemplateButton {
    type: 'URL' | 'PHONE_NUMBER' | 'QUICK_REPLY';
    text: string;
    url?: string;
    phone_number?: string;
}

interface LuaTemplate {
    name: string;
    category: 'MARKETING' | 'UTILITY' | 'AUTHENTICATION';
    language: string;
    header?: {
        type: 'TEXT' | 'IMAGE' | 'VIDEO' | 'DOCUMENT';
        text?: string;
    };
    body: string;
    footer?: string;
    buttons?: LuaTemplateButton[];
}

export class WhatsAppTemplateManager {
    private apiUrl = process.env.LUA_API_URL || 'https://api.heylua.ai';
    private apiKey = process.env.LUA_WEBHOOK_API_KEY || '';

    async createTemplate(template: LuaTemplate) {
        try {
            const response = await axios.post(
                `${this.apiUrl}/v1/templates`,
                template,
                {
                    headers: {
                        'Authorization': `Bearer ${this.apiKey}`,
                        'Content-Type': 'application/json'
                    }
                }
            );
            console.log(`✅ Template created: ${template.name}`);
            return response.data;
        } catch (error: any) {
            console.error(`❌ Failed to create template ${template.name}:`, error.response?.data || error.message);
            throw error;
        }
    }

    async createAllTemplates() {
        const templates = this.getAllTemplates();
        const results = [];

        for (const template of templates) {
            try {
                const result = await this.createTemplate(template);
                results.push({ success: true, name: template.name, result });
            } catch (error) {
                results.push({ success: false, name: template.name, error });
            }
        }

        return results;
    }

    getAllTemplates(): LuaTemplate[] {
        return [
            this.staffInvitationTemplate(),
            this.schedulePublicationTemplate(),
            this.shiftChecklistTemplate(),
            this.shiftReminderTemplate(),
            this.clockInReminderTemplate(),
            this.shiftUpdateTemplate(),
            this.checklistUpdateTemplate(),
            this.staffClockInTemplate(),
            this.clockInLocationRequestTemplate(),
            this.staffClockOutTemplate(),
            this.clockInFlowTemplate(),
            this.clockOutFlowTemplate(),
            this.voiceIncidentTemplate(),
            this.checklistStartTemplate(),
            this.checklistItemTemplate(),
            this.checklistPhotoRequestTemplate(),
            this.checklistCompleteTemplate()
        ];
    }

    // 1. Staff Invitation Template
    staffInvitationTemplate(): LuaTemplate {
        return {
            name: 'staff_invitation',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '👋 Welcome to {{1}}'
            },
            body: `Hi {{1}}!

You've been invited to join {{2}} on Mizan AI.

Get started by completing your registration:`,
            footer: 'Mizan AI - Restaurant Management',
            buttons: [
                {
                    type: 'URL',
                    text: 'Complete Registration',
                    url: '{{1}}'
                }
            ]
        };
    }

    // 2. Weekly Schedule Publication
    schedulePublicationTemplate(): LuaTemplate {
        return {
            name: 'weekly_schedule_published',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '📅 Your Schedule for {{1}}'
            },
            body: `Hi {{1}},

Your schedule for the week of {{2}} is now available.

Total shifts: {{3}}
Total hours: {{4}}

View your full schedule in the app.`,
            buttons: [
                {
                    type: 'URL',
                    text: 'View Schedule',
                    url: '{{1}}'
                }
            ]
        };
    }

    // 3. Shift Checklist (1 hour before)
    shiftChecklistTemplate(): LuaTemplate {
        return {
            name: 'shift_checklist_reminder',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '📋 Shift Checklist Ready'
            },
            body: `Hi {{1}},

Your shift starts in 1 hour at {{2}}.

Checklist: {{3}}
Tasks: {{4}}

Review your checklist before starting.`,
            buttons: [
                {
                    type: 'URL',
                    text: 'View Checklist',
                    url: '{{1}}'
                }
            ]
        };
    }

    // 4. Shift Reminder (30 mins before)
    shiftReminderTemplate(): LuaTemplate {
        return {
            name: 'shift_reminder_30min',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '⏰ Shift Starting Soon'
            },
            body: `Hi {{1}},

Your {{2}} shift starts in 30 minutes at {{3}}.

Location: {{4}}
Duration: {{5}}

See you soon!`,
            buttons: [
                {
                    type: 'QUICK_REPLY',
                    text: "I'm on my way"
                }
            ]
        };
    }

    // 5. Clock-In Reminder (10 mins before)
    clockInReminderTemplate(): LuaTemplate {
        return {
            name: 'clock_in_reminder',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '🕐 Time to Clock In'
            },
            body: `Hi {{1}},

Your shift starts in 10 minutes.

Ready to clock in?`,
            buttons: [
                {
                    type: 'QUICK_REPLY',
                    text: '✅ Clock In Now'
                }
            ]
        };
    }

    // 6. Shift Update Notification
    shiftUpdateTemplate(): LuaTemplate {
        return {
            name: 'shift_updated',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '🔄 Shift Updated'
            },
            body: `Hi {{1}},

Your shift on {{2}} has been updated.

New time: {{3}}
Changes: {{4}}

Please review the changes.`,
            buttons: [
                {
                    type: 'URL',
                    text: 'View Details',
                    url: '{{1}}'
                }
            ]
        };
    }

    // 7. Checklist Update Notification
    checklistUpdateTemplate(): LuaTemplate {
        return {
            name: 'checklist_updated',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '📝 Checklist Updated'
            },
            body: `Hi {{1}},

The checklist for your {{2}} shift has been updated.

New tasks: {{3}}
Modified tasks: {{4}}

Please review before your shift.`,
            buttons: [
                {
                    type: 'URL',
                    text: 'View Checklist',
                    url: '{{1}}'
                }
            ]
        };
    }

    // 8. Clock-In Flow (with location)
    clockInFlowTemplate(): LuaTemplate {
        return {
            name: 'clock_in_flow',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '📍 Clock In'
            },
            body: `Hi {{1}},

Ready to start your shift?

Please share your current location to clock in.`,
            buttons: [
                {
                    type: 'QUICK_REPLY',
                    text: '📍 Share Location'
                }
            ]
        };
    }

    // 9. Clock-Out Flow
    clockOutFlowTemplate(): LuaTemplate {
        return {
            name: 'clock_out_flow',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '✅ End Shift'
            },
            body: `Hi {{1}},

Your shift is ending.

Time worked: {{2}}
Tasks completed: {{3}}

Ready to clock out?`,
            buttons: [
                {
                    type: 'QUICK_REPLY',
                    text: '✅ Clock Out'
                },
                {
                    type: 'QUICK_REPLY',
                    text: '⏰ Need More Time'
                }
            ]
        };
    }

    // 10. Voice Incident Reporting
    voiceIncidentTemplate(): LuaTemplate {
        return {
            name: 'voice_incident_report',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '🎙️ Report an Incident'
            },
            body: `Hi {{1}},

To report an incident:
1. Record a voice message describing the issue
2. Include details: what, where, when
3. Send photos if helpful

We'll create a ticket and notify management.`,
            buttons: [
                {
                    type: 'QUICK_REPLY',
                    text: '🎤 Record Voice Report'
                }
            ]
        };
    }

    // 11. Checklist Start (after clock-in)
    checklistStartTemplate(): LuaTemplate {
        return {
            name: 'checklist_start',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '📋 Time for Your Checklist'
            },
            body: `Hi {{1}},

You have {{2}} checklist items to complete.

Estimated time: {{3}} minutes

Let's get started! 👇`,
            buttons: [
                {
                    type: 'QUICK_REPLY',
                    text: '✅ Start Checklist'
                }
            ]
        };
    }

    // 12. Checklist Item
    checklistItemTemplate(): LuaTemplate {
        return {
            name: 'checklist_item',
            category: 'UTILITY',
            language: 'en',
            body: `{{1}}/{{2}}: {{3}}

Please reply: YES, NO, or NA`
        };
    }

    // 13. Checklist Photo Request
    checklistPhotoRequestTemplate(): LuaTemplate {
        return {
            name: 'checklist_photo_request',
            category: 'UTILITY',
            language: 'en',
            body: `📷 Please send a photo as evidence for this item.

Tap the camera icon to take a photo.`
        };
    }

    // 14. Checklist Complete
    checklistCompleteTemplate(): LuaTemplate {
        return {
            name: 'checklist_complete',
            category: 'UTILITY',
            language: 'en',
            header: {
                type: 'TEXT',
                text: '✅ Checklist Complete!'
            },
            body: `Great job, {{1}}!

{{2}} finished:
• Completed: {{3}} items
• Time taken: {{4}} minutes
• Photos submitted: {{5}}

Your responses have been recorded. Thank you! 👍`
        };
    }

    // 15. Staff Clock-In Template
    staffClockInTemplate(): LuaTemplate {
        return {
            name: 'staff_clock_in',
            category: 'UTILITY',
            language: 'en',
            body: `⏰ *Clock-In* Hi *{{1}}*, Please *Clock-In* as your shift starts at *{{2}}* ({{3}} from now). 📍 *Location:* {{4}} 🧩 *Shift...`,
            buttons: [
                {
                    type: 'QUICK_REPLY',
                    text: 'Clock-In'
                }
            ]
        };
    }

    // 16. Clock-In Location Request Template
    clockInLocationRequestTemplate(): LuaTemplate {
        return {
            name: 'clock_in_location_request',
            category: 'UTILITY',
            language: 'en',
            body: `📍 Please share your *location* to clock in.`,
            buttons: [
                {
                    type: 'QUICK_REPLY',
                    text: 'Share Location'
                }
            ]
        };
    }

    // 17. Staff Clock-Out Template
    staffClockOutTemplate(): LuaTemplate {
        return {
            name: 'staff_clock_out',
            category: 'UTILITY',
            language: 'en',
            body: `✅ *Clock-Out* Hi *{{1}}*, ready to end your shift? You've worked for {{2}} today.`,
            buttons: [
                {
                    type: 'QUICK_REPLY',
                    text: 'Clock-Out'
                }
            ]
        };
    }
}

// CLI Usage
if (require.main === module) {
    const manager = new WhatsAppTemplateManager();

    console.log('🚀 Creating WhatsApp Templates via Lua API...\n');

    manager.createAllTemplates()
        .then(results => {
            console.log('\n📊 Results:');
            const successful = results.filter(r => r.success).length;
            const failed = results.filter(r => !r.success).length;

            console.log(`✅ Successful: ${successful}`);
            console.log(`❌ Failed: ${failed}`);

            if (failed > 0) {
                console.log('\nFailed templates:');
                results.filter(r => !r.success).forEach(r => {
                    console.log(`  - ${r.name}`);
                });
            }
        })
        .catch(error => {
            console.error('❌ Error creating templates:', error);
            process.exit(1);
        });
}

export default WhatsAppTemplateManager;
