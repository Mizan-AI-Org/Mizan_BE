/**
 * Data Migration Job Example
 * 
 * This is a one-time job that runs at a specific date/time to migrate data.
 * Demonstrates: One-time scheduling, data migration, and batch processing.
 */

import { LuaJob, Data } from "lua-cli";

const dataMigrationJob = new LuaJob({
  name: "migrate-user-schema",
  version: "1.0.0",
  description: "One-time migration to new user event schema",
  context: "Scheduled one-time task to migrate user events from old schema to new schema. " +
           "Runs once at the specified time and then automatically deactivates.",

  // One-time schedule: Run at specific date/time
  schedule: {
    type: 'once',
    executeAt: new Date('2025-12-31T02:00:00Z')  // December 31, 2025 at 2 AM UTC
  },

  // Give it 30 minutes for large migration
  timeout: 1800,

  // Retry important migrations
  retry: {
    maxAttempts: 3,
    backoffSeconds: 300  // 5 minutes between retries
  },

  execute: async () => {
    console.log('🔄 Starting data migration...');
    const startTime = Date.now();

    let totalMigrated = 0;
    let totalErrors = 0;

    try {
      // Get all user events with old schema
      const oldEventsResponse = await Data.get('user-events', {}, 1, 1000);
      const oldEvents = oldEventsResponse.data || [];
      
      console.log(`📊 Found ${oldEvents.length} events to migrate`);

      // Migrate each event
      for (const event of oldEvents) {
        try {
          // Transform old schema to new schema
          const migratedEvent = {
            // New fields
            eventId: event.id,
            eventType: event.data.type || 'unknown',
            userId: event.data.userId || event.data.user_id,  // Handle both formats
            email: event.data.email,
            name: event.data.name,
            
            // Preserve metadata
            metadata: {
              ...event.data.metadata,
              migratedFrom: 'old-schema',
              migratedAt: new Date().toISOString()
            },
            
            // Copy timestamps
            originalTimestamp: event.data.timestamp,
            receivedAt: event.data.receivedAt
          };

          // Create in new collection
          await Data.create('user-events-v2', migratedEvent, 
            `${migratedEvent.eventType} ${migratedEvent.email}`
          );

          totalMigrated++;

          // Log progress every 100 records
          if (totalMigrated % 100 === 0) {
            console.log(`✅ Migrated ${totalMigrated} records...`);
          }

        } catch (error) {
          console.error(`❌ Failed to migrate event ${event.id}:`, error);
          totalErrors++;
        }
      }

      const duration = Date.now() - startTime;
      const successRate = ((totalMigrated / oldEvents.length) * 100).toFixed(2);

      console.log(`✅ Migration complete!`);
      console.log(`📊 Stats:`);
      console.log(`   - Total records: ${oldEvents.length}`);
      console.log(`   - Migrated: ${totalMigrated}`);
      console.log(`   - Errors: ${totalErrors}`);
      console.log(`   - Success rate: ${successRate}%`);
      console.log(`   - Duration: ${duration}ms`);

      // Store migration report
      await Data.create('migration-reports', {
        jobName: 'migrate-user-schema',
        totalRecords: oldEvents.length,
        migrated: totalMigrated,
        errors: totalErrors,
        successRate: parseFloat(successRate),
        duration,
        completedAt: new Date().toISOString()
      });

      return {
        success: true,
        totalRecords: oldEvents.length,
        migrated: totalMigrated,
        errors: totalErrors,
        successRate: `${successRate}%`,
        duration: `${duration}ms`
      };

    } catch (error) {
      console.error('💥 Migration failed:', error);
      
      return {
        success: false,
        error: String(error),
        totalMigrated,
        totalErrors
      };
    }
  }
});

export default dataMigrationJob;

