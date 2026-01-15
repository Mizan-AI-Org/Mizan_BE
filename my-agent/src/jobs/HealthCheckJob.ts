/**
 * Health Check Job Example
 * 
 * This job runs every 5 minutes to check system health and alert on issues.
 * Demonstrates: Interval scheduling, monitoring, and alerting.
 */

import { LuaJob, Data, Products } from "lua-cli";

const healthCheckJob = new LuaJob({
  // Use a distinct name to avoid collision with an existing job on the server
  name: "health-check-dev",
  description: "System health monitoring",
  // Interval schedule: Every 5 minutes
  schedule: {
    type: 'interval',
    seconds: 300  // 5 minutes = 300 seconds
  },

  // Quick timeout for health checks
  timeout: 30,

  execute: async () => {
    console.log('❤️ Running health check...');
    const checks: any = {};

    // Check 1: Database connectivity
    try {
      const testQuery = await Data.get('health-checks', {}, 1, 1);
      checks.database = {
        status: 'healthy',
        responseTime: Date.now()
      };
    } catch (error) {
      checks.database = {
        status: 'unhealthy',
        error: String(error)
      };
    }

    // Check 2: Products API
    try {
      const startTime = Date.now();
      await Products.get(1, 1);
      const responseTime = Date.now() - startTime;
      
      checks.productsApi = {
        status: 'healthy',
        responseTime: `${responseTime}ms`
      };
    } catch (error) {
      checks.productsApi = {
        status: 'unhealthy',
        error: String(error)
      };
    }

    // Determine overall health
    const allHealthy = Object.values(checks).every((check: any) => check.status === 'healthy');
    const overallStatus = allHealthy ? 'healthy' : 'degraded';

    // Log health check result
    await Data.create('health-checks', {
      status: overallStatus,
      checks,
      timestamp: new Date().toISOString()
    });

    // Alert if unhealthy (in production, send to monitoring service)
    if (!allHealthy) {
      console.warn('⚠️ System health degraded:', checks);
      // TODO: Send alert to monitoring service
    }

    return {
      status: overallStatus,
      checks,
      timestamp: new Date().toISOString()
    };
  }
});

export default healthCheckJob;

