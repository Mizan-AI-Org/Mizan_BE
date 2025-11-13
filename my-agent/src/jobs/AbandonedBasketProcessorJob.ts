/**
 * Abandoned Basket Processor Job
 * 
 * This job runs every 15 minutes to process basket checkout reminders.
 * Works together with CreateSmartBasketTool to send abandoned cart notifications.
 * 
 * Flow:
 * 1. User creates basket → Tool schedules reminder in custom data
 * 2. This job runs every 15 minutes
 * 3. Checks which reminders are due
 * 4. Checks if baskets were checked out
 * 5. Sends reminders for abandoned baskets
 */

import { LuaJob, Data, Baskets } from "lua-cli";

const abandonedBasketProcessorJob = new LuaJob({
  name: "process-basket-reminders",
  version: "1.0.0",
  description: "Processes abandoned basket reminders",
  context: "Runs every 15 minutes to check for abandoned baskets and send recovery emails. " +
           "Works with basket creation tools that schedule reminders.",

  // Run every 15 minutes
  schedule: {
    type: 'interval',
    seconds: 900  // 15 minutes = 900 seconds
  },

  timeout: 120,  // 2 minutes

  execute: async () => {
    console.log('🔍 Processing basket reminders...');

    // Get all pending reminders
    const remindersResponse = await Data.get('basket-reminders', {}, 1, 500);
    const reminders = remindersResponse.data || [];
    const now = new Date();

    let processedCount = 0;
    let abandonedCount = 0;
    let checkedOutCount = 0;

    for (const reminder of reminders) {
      // Skip if not yet time
      const scheduledTime = new Date(reminder.data.scheduledFor);
      if (scheduledTime > now) continue;

      // Skip if already processed
      if (reminder.data.status !== 'pending') continue;

      try {
        // Get the basket
        const basketInstance = await Baskets.getById(reminder.data.basketId);
        const basket = await basketInstance.getData();

        // Check basket status
        const isAbandoned = basket.status === 'active';

        if (isAbandoned) {
          // Basket is abandoned - send reminder
          console.log(`🛒 Abandoned basket found: ${basket.id}`);
          abandonedCount++;

          // Calculate basket value
          const totalValue = basket.items?.reduce((sum: number, item: any) => {
            return sum + (item.price * item.quantity);
          }, 0) || 0;

          // Log abandoned basket
          await Data.create('abandoned-baskets', {
            basketId: basket.id,
            currency: basket.currency,
            itemCount: basket.items?.length || 0,
            totalValue,
            items: basket.items,
            abandonedAt: new Date().toISOString(),
            reminderSent: true
          });

          // Update reminder status
          await Data.update('basket-reminders', reminder.id, {
            ...reminder.data,
            status: 'completed',
            result: 'abandoned',
            processedAt: new Date().toISOString()
          });

          // TODO: Send actual reminder email/SMS
          console.log(`📧 Would send reminder: "You left ${basket.items?.length || 0} items in your cart!"`);

        } else {
          // Basket was checked out
          console.log(`✅ Basket ${basket.id} was checked out (${basket.status})`);
          checkedOutCount++;

          // Update reminder status
          await Data.update('basket-reminders', reminder.id, {
            ...reminder.data,
            status: 'completed',
            result: 'checked-out',
            processedAt: new Date().toISOString()
          });
        }

        processedCount++;

      } catch (error) {
        console.error(`❌ Error processing basket ${reminder.data.basketId}:`, error);
        
        // Mark as error
        try {
          await Data.update('basket-reminders', reminder.id, {
            ...reminder.data,
            status: 'error',
            error: String(error),
            processedAt: new Date().toISOString()
          });
        } catch (updateError) {
          console.error('Failed to update reminder status:', updateError);
        }
      }
    }

    console.log('✅ Reminder processing complete');
    console.log(`📊 Processed: ${processedCount}, Abandoned: ${abandonedCount}, Checked out: ${checkedOutCount}`);

    return {
      success: true,
      processedCount,
      abandonedCount,
      checkedOutCount,
      timestamp: new Date().toISOString()
    };
  }
});

export default abandonedBasketProcessorJob;

