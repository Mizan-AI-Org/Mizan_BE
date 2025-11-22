/**
 * Staff Management Events Webhook
 * 
 * Manages restaurant staff operations and workforce coordination:
 * - Clock in/out tracking
 * - Table assignments and handoffs
 * - Break management
 * - Performance tracking
 * - Task assignments and completion
 * - Staff alerts and communications
 * 
 * Webhook URL: {API_URL}/webhooks/{agentId}/{webhookId}
 */

import { LuaWebhook, Data } from "lua-cli";
import { StaffManagementModule } from "../modules/staff-management-events";
import { z } from "zod";

const staffManagementWebhook = new LuaWebhook({
    name: "staff-management-events",
    version: "2.0.0",
    description: "Manages staff scheduling, assignments, and performance tracking",
    context: "This webhook optimizes staff operations by tracking assignments, monitoring workload, " +
        "and ensuring efficient task distribution across the team.",

    querySchema: z.object({
        shift: z.enum(['breakfast', 'lunch', 'dinner', 'late_night']).optional(),
        department: z.enum(['foh', 'boh', 'bar', 'management']).optional()
    }),

    headerSchema: z.object({
        'x-api-key': z.string(),
        'x-role': z.enum(['server', 'bartender', 'host', 'busser', 'manager', 'chef', 'cook', 'dishwasher']),
        'x-manager-id': z.string().optional(),
        'content-type': z.string().optional()
    }),

    bodySchema: z.object({
        eventType: z.enum([
            'clock_in',
            'clock_out',
            'break_start',
            'break_end',
            'table_assigned',
            'table_transferred',
            'section_assigned',
            'task_assigned',
            'task_completed',
            'alert_triggered',
            'performance_logged',
            'tip_reported',
            'incident_reported',
            'shift_change'
        ]),
        staffId: z.string(),
        staffName: z.string(),
        role: z.enum(['server', 'bartender', 'host', 'busser', 'manager', 'chef', 'cook', 'dishwasher']),
        details: z.object({
            tableIds: z.array(z.string()).optional(),
            sectionId: z.string().optional(),
            taskDescription: z.string().optional(),
            taskPriority: z.enum(['low', 'medium', 'high', 'urgent']).optional(),
            alertType: z.string().optional(),
            alertMessage: z.string().optional(),
            performanceMetric: z.object({
                metric: z.string(),
                value: z.number(),
                target: z.number().optional()
            }).optional(),
            tipAmount: z.number().optional(),
            incidentDescription: z.string().optional(),
            transferredTo: z.string().optional(),
            breakDuration: z.number().optional() // in minutes
        }).optional(),
        metadata: z.record(z.any()).optional(),
        timestamp: z.string()
    }),

    execute: async ({ query, headers, body }) => {
        console.log(`👥 [Staff] ${body.eventType} - ${body.staffName} (${body.role})`);

        const expectedKey = process.env.WEBHOOK_API_KEY;
        if (!expectedKey) {
            throw new Error('API key is not configured in the environment variables');
        }
        if (headers['x-api-key'] !== expectedKey) {
            throw new Error('Unauthorized: Invalid API key');
        }

        const role = headers['x-role'];
        const eventPermissions: Record<string, string[]> = {
            clock_in: ['server', 'bartender', 'host', 'busser', 'chef', 'cook', 'dishwasher', 'manager'],
            clock_out: ['server', 'bartender', 'host', 'busser', 'chef', 'cook', 'dishwasher', 'manager'],
            break_start: ['server', 'bartender', 'host', 'busser', 'chef', 'cook', 'dishwasher', 'manager'],
            break_end: ['server', 'bartender', 'host', 'busser', 'chef', 'cook', 'dishwasher', 'manager'],
            table_assigned: ['manager', 'host'],
            table_transferred: ['manager', 'host'],
            section_assigned: ['manager'],
            task_assigned: ['manager'],
            task_completed: ['server', 'bartender', 'host', 'busser', 'chef', 'cook', 'dishwasher', 'manager'],
            alert_triggered: ['server', 'bartender', 'host', 'busser', 'chef', 'cook', 'dishwasher', 'manager'],
            performance_logged: ['manager'],
            tip_reported: ['server', 'bartender', 'manager'],
            incident_reported: ['server', 'bartender', 'host', 'busser', 'chef', 'cook', 'dishwasher', 'manager'],
            shift_change: ['manager']
        };
        const allowedRoles = eventPermissions[body.eventType];
        if (allowedRoles && !allowedRoles.includes(role)) {
            throw new Error(`Forbidden: role ${role} not permitted for event ${body.eventType}`);
        }

        const start = Date.now();
        const module = new StaffManagementModule();
        const { actionTaken, requiresManagerAttention, workloadImpact, workloadScore, recommendations } = module.processEvent({
            eventType: body.eventType as any,
            staffId: body.staffId,
            staffName: body.staffName,
            role: body.role,
            details: body.details || {},
            timestamp: body.timestamp
        });

        console.log(`   📋 Action: ${actionTaken}`);

        // Performance monitoring
        const latencyMs = Date.now() - start;
        try {
            await Data.create('module-metrics', {
                module: 'staff-management',
                eventType: body.eventType,
                latencyMs,
                timestamp: new Date().toISOString()
            });
        } catch (e) {
            console.warn('Failed to record module metrics', e);
        }

        // Store staff event
        const eventData = {
            ...body,
            shift: query?.shift,
            department: query?.department,
            managerId: headers['x-manager-id'],
            actionTaken,
            requiresManagerAttention,
            workloadImpact,
            workloadScore,
            receivedAt: new Date().toISOString(),
            processed: true
        };

        let result;
        for (let attempt = 1; attempt <= 3; attempt++) {
            try {
                result = await Data.create(
                    'staff-management-events',
                    eventData,
                    `${body.eventType} ${body.staffName} ${body.role}`
                );
                break;
            } catch (err) {
                console.error(`Attempt ${attempt}: Failed to store staff event`, err);
                if (attempt === 3) throw new Error('Failed to store staff event after 3 attempts');
                await new Promise(r => setTimeout(r, attempt * 500));
            }
        }

        console.log(`✅ Staff event processed: ${result.id}`);

        // Return response with workload insights
        return {
            success: true,
            eventId: result.id,
            staffId: body.staffId,
            staffName: body.staffName,
            actionTaken,
            workloadScore,
            requiresManagerAttention,
            recommendations,
            timestamp: new Date().toISOString()
        };
    }
});

/**
 * Calculate simplified workload score (0-100)
 * In production, this would query active assignments, table counts, etc.
 */
// Workload calculation delegated to module

export default staffManagementWebhook;