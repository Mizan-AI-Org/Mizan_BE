/**
 * Forecasting Events Webhook
 * 
 * Handles predictive analytics and forecasting for restaurant operations:
 * - Demand forecasting (covers, revenue)
 * - Inventory predictions and reorder alerts
 * - Staffing recommendations
 * - Sales trends and anomaly detection
 * - Seasonal pattern analysis
 * 
 * Webhook URL: {API_URL}/webhooks/{agentId}/{webhookId}
 */

import { LuaWebhook, Data } from "lua-cli";
import { ForecastingModule } from "../modules/forecasting-events";
import { z } from "zod";

const forecastingWebhook = new LuaWebhook({
    name: "forecasting-events",
    version: "1.0.0",
    description: "Processes forecasting data and generates predictive insights",
    context: "This webhook receives historical data and external factors to generate accurate forecasts " +
        "for inventory, staffing, and revenue. It helps optimize procurement and reduce waste.",

    querySchema: z.object({
        forecastType: z.enum(['demand', 'inventory', 'staffing', 'revenue']).optional(),
        timeHorizon: z.enum(['daily', 'weekly', 'monthly']).optional()
    }),

    headerSchema: z.object({
        'x-api-key': z.string(),
        'x-role': z.enum(['analyst', 'manager']),
        'x-forecast-source': z.string().optional(), // 'system', 'manual', 'integration'
        'content-type': z.string().optional()
    }),

    bodySchema: z.object({
        eventType: z.enum([
            'forecast_generated',
            'trend_detected',
            'anomaly_detected',
            'reorder_alert',
            'capacity_warning',
            'demand_spike',
            'inventory_optimization',
            'staffing_recommendation',
            'seasonal_pattern',
            'forecast_accuracy_check'
        ]),
        forecastPeriod: z.object({
            startDate: z.string(),
            endDate: z.string(),
            granularity: z.enum(['hourly', 'daily', 'weekly'])
        }),
        predictions: z.object({
            covers: z.number().optional(),
            revenue: z.number().optional(),
            avgCheckSize: z.number().optional(),
            peakHours: z.array(z.string()).optional(),
            topItems: z.array(z.object({
                itemId: z.string(),
                itemName: z.string(),
                predictedQuantity: z.number(),
                confidence: z.number() // 0-1
            })).optional(),
            staffingNeeds: z.object({
                servers: z.number(),
                kitchen: z.number(),
                support: z.number()
            }).optional()
        }).optional(),
        inventoryAlerts: z.array(z.object({
            itemId: z.string(),
            itemName: z.string(),
            currentStock: z.number(),
            predictedUsage: z.number(),
            reorderPoint: z.number(),
            recommendedOrder: z.number(),
            urgency: z.enum(['low', 'medium', 'high', 'critical'])
        })).optional(),
        trends: z.object({
            direction: z.enum(['up', 'down', 'stable']),
            magnitude: z.number(), // percentage change
            category: z.string(),
            drivers: z.array(z.string()).optional()
        }).optional(),
        anomaly: z.object({
            metric: z.string(),
            expectedValue: z.number(),
            actualValue: z.number(),
            deviation: z.number(), // percentage
            severity: z.enum(['minor', 'moderate', 'severe'])
        }).optional(),
        confidence: z.number().min(0).max(1), // Overall forecast confidence
        dataQuality: z.number().min(0).max(1).optional(),
        externalFactors: z.object({
            weather: z.string().optional(),
            events: z.array(z.string()).optional(),
            holidays: z.array(z.string()).optional(),
            competitorActivity: z.string().optional()
        }).optional(),
        metadata: z.record(z.any()).optional(),
        timestamp: z.string()
    }),

    execute: async ({ query, headers, body }) => {
        console.log(`📊 [Forecasting] ${body.eventType} - Confidence: ${(body.confidence * 100).toFixed(1)}%`);

        const expectedKey = process.env.WEBHOOK_API_KEY;
        if (!expectedKey) {
            throw new Error('API key is not configured in the environment variables');
        }
        if (headers['x-api-key'] !== expectedKey) {
            throw new Error('Unauthorized: Invalid API key');
        }

        const role = headers['x-role'];
        const eventPermissions: Record<string, string[]> = {
            forecast_generated: ['analyst', 'manager'],
            trend_detected: ['analyst', 'manager'],
            anomaly_detected: ['analyst', 'manager'],
            reorder_alert: ['manager'],
            capacity_warning: ['manager'],
            demand_spike: ['manager'],
            inventory_optimization: ['analyst', 'manager'],
            staffing_recommendation: ['manager'],
            seasonal_pattern: ['analyst', 'manager'],
            forecast_accuracy_check: ['analyst', 'manager']
        };
        const allowedRoles = eventPermissions[body.eventType];
        if (allowedRoles && !allowedRoles.includes(role)) {
            throw new Error(`Forbidden: role ${role} not permitted for event ${body.eventType}`);
        }

        const start = Date.now();
        const module = new ForecastingModule();
        const { actionTaken, requiresAction, priority, recommendations } = module.processEvent({
            eventType: body.eventType as any,
            forecastPeriod: body.forecastPeriod,
            predictions: body.predictions,
            inventoryAlerts: body.inventoryAlerts,
            trends: body.trends,
            anomaly: body.anomaly,
            confidence: body.confidence,
            dataQuality: body.dataQuality,
            externalFactors: body.externalFactors
        });

        console.log(`   📋 Action: ${actionTaken}`);

        // Store forecasting event with insights
        const eventData = {
            ...body,
            forecastType: query?.forecastType,
            timeHorizon: query?.timeHorizon,
            source: headers['x-forecast-source'] || 'system',
            actionTaken,
            requiresAction,
            priority,
            recommendations,
            receivedAt: new Date().toISOString(),
            processed: true
        };

        // Performance monitoring
        const latencyMs = Date.now() - start;
        try {
            await Data.create('module-metrics', {
                module: 'forecasting',
                eventType: body.eventType,
                latencyMs,
                timestamp: new Date().toISOString()
            });
        } catch (e) {
            console.warn('Failed to record module metrics', e);
        }

        let result;
        for (let attempt = 1; attempt <= 3; attempt++) {
            try {
                result = await Data.create(
                    'forecasting-events',
                    eventData,
                    `${body.eventType} ${body.forecastPeriod.startDate} confidence:${(body.confidence * 100).toFixed(0)}%`
                );
                break;
            } catch (err) {
                console.error(`Attempt ${attempt}: Failed to store forecasting event`, err);
                if (attempt === 3) throw new Error('Failed to store forecasting event after 3 attempts');
                await new Promise(r => setTimeout(r, attempt * 500));
            }
        }

        console.log(`✅ Forecast processed: ${result.id}`);

        // Calculate business impact
        const potentialSavings = body.inventoryAlerts?.reduce((sum, item) =>
            sum + (item.currentStock > item.reorderPoint ? 50 : 0), 0
        ) || 0;

        const revenueImpact = body.predictions?.revenue
            ? (body.predictions.revenue * 0.15).toFixed(0) // 15% potential impact
            : null;

        return {
            success: true,
            eventId: result.id,
            eventType: body.eventType,
            actionTaken,
            requiresAction,
            priority,
            confidence: `${(body.confidence * 100).toFixed(1)}%`,
            recommendations,
            insights: {
                potentialSavings: potentialSavings > 0 ? `$${potentialSavings}` : null,
                revenueImpact: revenueImpact ? `$${revenueImpact}` : null,
                dataQuality: body.dataQuality ? `${(body.dataQuality * 100).toFixed(1)}%` : null
            },
            timestamp: new Date().toISOString()
        };
    }
});

export default forecastingWebhook;