/**
 * Smart Basket Tool Example
 * 
 * This tool creates a basket AND schedules a follow-up job to check if it was abandoned.
 * Demonstrates: Dynamic job creation, basket management, and automated follow-ups.
 * 
 * Use case: E-commerce abandoned cart recovery
 */

import { LuaTool, Baskets, Jobs, Data } from "lua-cli";
import { z } from "zod";

export class CreateSmartBasketTool implements LuaTool {
  name = "create_smart_basket";
  description = "Creates a basket with automatic abandoned cart reminder";
  
  inputSchema = z.object({
    currency: z.string().default('USD')
  });

  async execute(input: { currency: string }) {
    console.log('🛒 Creating smart basket with auto-reminder...');

    // Step 1: Create the basket
    const basketData = {
      currency: input.currency,
      metadata: {
        createdBy: 'smart-basket-tool',
        reminderScheduled: true,
        createdAt: new Date().toISOString()
      }
    };

    const basketInstance = await Baskets.create(basketData);
    const basketInfo = await basketInstance.getData();

    console.log(`✅ Basket created: ${basketInfo.id}`);

    // Step 2: Schedule a one-time job to check the basket in 3 hours
    const checkTime = new Date(Date.now() + 3 * 60 * 60 * 1000); // 3 hours from now

    // Capture basketId in closure for the job
    const basketId = basketInfo.id;

    // Create a dynamic one-time job using the Jobs API!
    
    const job = await Jobs.create({
      name: `check-basket-${basketId}`,
      description: `Check if basket ${basketId} was checked out`,
      
      // One-time job that runs in 3 hours
      schedule: {
        type: 'once',
        executeAt: checkTime
      },
      
      timeout: 60,  // 1 minute timeout
      
      // Write normal TypeScript code! It will be compiled automatically
      execute: async () => {
        console.log(`⏰ Checking basket ${basketId}...`);
        
        const basketInstance = await Baskets.getById(basketId);
        const basket = await basketInstance.getData();
        
        if (basket.status === 'active' || basket.status === 'pending') {
          // Basket is still active - it was abandoned!
          console.log(`🛒 Basket ${basketId} was abandoned`);
          
          await Data.create('abandoned-baskets', {
            basketId: basketId,
            currency: basket.currency,
            itemCount: basket.items?.length || 0,
            totalValue: basket.total || 0,
            abandonedAt: new Date().toISOString()
          });
          
          console.log('📧 Sending abandoned cart reminder email...');
          // TODO: Integrate with email service
          // await sendEmail({
          //   to: userEmail,
          //   template: 'abandoned-cart',
          //   data: { basket }
          // });
          
          return { 
            abandoned: true, 
            basketId: basketId,
            reminderSent: true 
          };
        }
        
        console.log(`✅ Basket ${basketId} was checked out`);
        return { 
          abandoned: false, 
          basketId: basketId,
          status: basket.status 
        };
      }
    });

    console.log(`⏰ One-time job created: ${job.jobId}`);
    console.log(`📅 Will execute at: ${checkTime.toLocaleString()}`);

    // Return basket info with job details
    return {
      success: true,
      basket: {
        id: basketInfo.id,
        currency: basketInfo.currency,
        status: basketInfo.status
      },
      scheduledJob: {
        jobId: job.jobId,
        name: job.name,
        scheduledFor: checkTime.toISOString(),
        message: 'One-time job will check basket in 3 hours'
      }
    };
  }
}

/**
 * Check Abandoned Baskets Tool
 * 
 * This tool can be called manually or by a recurring job to check for abandoned baskets.
 */
export class CheckAbandonedBasketsTool implements LuaTool {
  name = "check_abandoned_baskets";
  description = "Checks for abandoned baskets and sends reminders";
  
  inputSchema = z.object({
    basketId: z.string().optional()
  });

  async execute(input: { basketId?: string }) {
    console.log('🔍 Checking for abandoned baskets...');

    // Get all abandoned baskets from data collection
    const abandonedBasketsData = await Data.get('abandoned-baskets', {}, 1, 100);
    const abandonedBaskets = abandonedBasketsData.data || [];

    if (input.basketId) {
      // Check specific basket
      const specific = abandonedBaskets.find((ab: any) => ab.data.basketId === input.basketId);
      
      if (specific) {
        return {
          basketId: input.basketId,
          abandoned: true,
          abandonedAt: specific.data.abandonedAt,
          itemCount: specific.data.itemCount,
          totalValue: specific.data.totalValue
        };
      }

      // Not in abandoned list - check current status
      const basketInstance = await Baskets.getById(input.basketId);
      const basket = await basketInstance.getData();

      return {
        basketId: input.basketId,
        abandoned: false,
        status: basket.status,
        itemCount: basket.items?.length || 0
      };
    }

    // Return all abandoned baskets summary
    const summary = abandonedBaskets.map((ab: any) => ({
      basketId: ab.data.basketId,
      currency: ab.data.currency,
      itemCount: ab.data.itemCount,
      totalValue: ab.data.totalValue,
      abandonedAt: ab.data.abandonedAt
    }));

    console.log(`🛒 Found ${abandonedBaskets.length} abandoned baskets`);

    return {
      success: true,
      abandonedCount: abandonedBaskets.length,
      abandonedBaskets: summary,
      timestamp: new Date().toISOString()
    };
  }
}

