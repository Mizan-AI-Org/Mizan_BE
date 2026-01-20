/**
 * Daily Cleanup Job Example
 * 
 * This job runs daily at 2 AM to clean up old data and maintain database performance.
 * Demonstrates: Cron scheduling, data cleanup, and error handling.
 */

import { LuaJob, Data } from "lua-cli";

const dailyCleanupJob = new LuaJob({
  name: "daily-cleanup",
  description: "Daily database cleanup and maintenance",
  // Cron schedule: Every day at 2 AM EST
  schedule: {
    type: 'cron',
    expression: '0 2 * * *',  // minute hour day month day-of-week
    timezone: 'America/New_York'
  },

  // Give it 10 minutes to complete
  timeout: 600,

  // Retry up to 3 times if it fails
  retry: {
    maxAttempts: 3,
    backoffSeconds: 60  // Wait 60s between retries
  },

  execute: async () => {
    console.log('🧹 Starting daily cleanup...');
    const startTime = Date.now();

    let totalRecordsDeleted = 0;

    // 1. Clean up old user events (older than 90 days)
    const ninetyDaysAgo = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000);
    
    try {
      const oldEventsResponse = await Data.get('user-events', {}, 1, 1000);
      const oldEvents = oldEventsResponse.data || [];
      
      for (const event of oldEvents) {
        const eventDate = new Date(event.data.receivedAt);
        if (eventDate < ninetyDaysAgo) {
          await Data.delete('user-events', event.id);
          totalRecordsDeleted++;
        }
      }
      
      console.log(`✅ Deleted ${totalRecordsDeleted} old user events`);
    } catch (error) {
      console.error('❌ Error cleaning user events:', error);
    }

    // 2. Clean up old payment logs (older than 180 days)
    const oneEightyDaysAgo = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000);
    
    try {
      const oldLogsResponse = await Data.get('payment-logs', {}, 1, 1000);
      const oldLogs = oldLogsResponse.data || [];
      let logsDeleted = 0;
      
      for (const log of oldLogs) {
        const logDate = new Date(log.data.receivedAt);
        if (logDate < oneEightyDaysAgo) {
          await Data.delete('payment-logs', log.id);
          logsDeleted++;
          totalRecordsDeleted++;
        }
      }
      
      console.log(`✅ Deleted ${logsDeleted} old payment logs`);
    } catch (error) {
      console.error('❌ Error cleaning payment logs:', error);
    }

    // 3. Clean up abandoned baskets (older than 7 days)
    // Note: This would require basket API support for deletion
    // For now, we'll just log the count
    
    const duration = Date.now() - startTime;
    
    console.log(`✅ Cleanup complete in ${duration}ms`);

    return {
      success: true,
      recordsDeleted: totalRecordsDeleted,
      duration: `${duration}ms`,
      completedAt: new Date().toISOString(),
      collections: ['user-events', 'payment-logs']
    };
  }
});

export default dailyCleanupJob;

