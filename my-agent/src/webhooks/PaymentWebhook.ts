/**
 * Payment Webhook Example
 * 
 * This webhook receives payment notifications from payment providers (Stripe, PayPal, etc.).
 * It validates signatures, processes payment events, and updates order status.
 * 
 * Common use case: Stripe webhook for payment confirmations
 */

import { LuaWebhook, Orders, Data } from "lua-cli";
import { z } from "zod";

const paymentWebhook = new LuaWebhook({
  name: "payment-notifications",
  version: "1.0.0",
  description: "Receives payment notifications from payment providers",
  context: "This webhook processes payment confirmation events from Stripe or other providers. " +
           "It validates the payment, updates order status, and logs the transaction.",

  // Headers typically include webhook signature for verification
  headerSchema: z.object({
    'stripe-signature': z.string().optional(),
    'content-type': z.string()
  }),

  // Payment event body
  bodySchema: z.object({
    type: z.string(),  // e.g., 'payment_intent.succeeded'
    data: z.object({
      object: z.object({
        id: z.string(),
        amount: z.number(),
        currency: z.string(),
        status: z.string(),
        metadata: z.object({
          orderId: z.string().optional(),
          customerId: z.string().optional()
        }).optional()
      })
    })
  }),

  execute: async ({ headers, body }) => {
    console.log(`💳 Payment event received:`, body.type);

    // In production: Verify Stripe signature
    // const signature = headers['stripe-signature'];
    // const isValid = verifyStripeSignature(body, signature);
    // if (!isValid) throw new Error('Invalid signature');

    const payment = body.data.object;
    const eventType = body.type;

    // Handle payment success
    if (eventType === 'payment_intent.succeeded') {
      console.log(`✅ Payment successful: ${payment.amount} ${payment.currency}`);

      // Update order if we have orderId in metadata
      if (payment.metadata?.orderId) {
        try {
          await Orders.updateStatus('confirmed', payment.metadata.orderId);
          console.log(`📦 Order ${payment.metadata.orderId} marked as confirmed`);
        } catch (error) {
          console.error('Failed to update order:', error);
        }
      }

      // Log the transaction
      await Data.create('payment-logs', {
        paymentId: payment.id,
        amount: payment.amount,
        currency: payment.currency,
        orderId: payment.metadata?.orderId,
        customerId: payment.metadata?.customerId,
        status: 'succeeded',
        receivedAt: new Date().toISOString()
      });

      return {
        success: true,
        paymentId: payment.id,
        orderId: payment.metadata?.orderId,
        message: 'Payment processed successfully'
      };
    }

    // Handle payment failure
    if (eventType === 'payment_intent.payment_failed') {
      console.log(`❌ Payment failed: ${payment.id}`);

      await Data.create('payment-logs', {
        paymentId: payment.id,
        status: 'failed',
        receivedAt: new Date().toISOString()
      });

      return {
        success: true,
        paymentId: payment.id,
        message: 'Payment failure logged'
      };
    }

    // Other event types
    return {
      success: true,
      message: `Event ${eventType} received`
    };
  }
});

export default paymentWebhook;

