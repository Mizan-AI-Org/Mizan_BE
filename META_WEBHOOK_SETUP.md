# Meta WhatsApp Webhook Configuration

## 🌐 Your Webhook URL

```
https://your-production-domain.com/api/notifications/whatsapp/webhook/
```

**Replace `your-production-domain.com` with your actual domain**

### Example URLs for Different Environments:

**Production:**
```
https://api.mizanapp.com/api/notifications/whatsapp/webhook/
```

**Staging:**
```
https://staging-api.mizanapp.com/api/notifications/whatsapp/webhook/
```

**Local Development (using ngrok):**
```
https://abc123.ngrok.io/api/notifications/whatsapp/webhook/
```

---

## 📋 Meta Developer Console Setup

### Step 1: Access WhatsApp Settings
1. Go to: https://developers.facebook.com/apps/
2. Select your app
3. Click **WhatsApp** → **Configuration** (left sidebar)

### Step 2: Configure Webhook
Click **"Configure webhooks"** button

Fill in the form:

| Field | Value |
|-------|-------|
| **Callback URL** | `https://your-domain.com/api/notifications/whatsapp/webhook/` |
| **Verify Token** | `your_secure_verify_token` (from your `.env` file) |

**Important**: The verify token must match `WHATSAPP_VERIFY_TOKEN` in your `.env` file

### Step 3: Subscribe to Webhook Fields
Select the following webhook fields:

- ✅ **messages** - Receive incoming messages
- ✅ **message_status** - Get delivery/read receipts (sent, delivered, read, failed)
- ✅ **message_template_status_update** - Template approval status

### Step 4: Verify Webhook
1. Click **"Verify and Save"**
2. Meta will send a GET request to your webhook with verification challenge
3. Your webhook will respond with the challenge
4. You should see: ✅ **"Webhook verified successfully"**

---

## 🔧 Webhook Implementation Details

### Your Django Webhook Handler

**File**: `notifications/views.py`  
**Function**: `whatsapp_webhook(request)`  
**URL Pattern**: `/api/notifications/whatsapp/webhook/`

### Handles:
- **GET requests**: Webhook verification from Meta
- **POST requests**: Incoming messages, status updates, button clicks, location shares

### Verification Logic:
```python
def whatsapp_webhook(request):
    if request.method == 'GET':
        # Verification challenge from Meta
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        if mode == 'subscribe' and token == settings.WHATSAPP_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type='text/plain')
        else:
            return HttpResponse('Forbidden', status=403)
    
    elif request.method == 'POST':
        # Process incoming WhatsApp messages
        # ... (your existing logic)
```

---

## 🧪 Testing Your Webhook

### 1. Test Verification (GET Request)
```bash
curl -X GET "https://your-domain.com/api/notifications/whatsapp/webhook/?hub.mode=subscribe&hub.verify_token=your_verify_token&hub.challenge=test123"
```

**Expected Response**: `test123`

### 2. Test Message Receipt (POST Request)
Send a test message from your phone to your WhatsApp Business number, and check logs:

```bash
tail -f /var/log/django/django.log
```

You should see:
```
[WhatsApp Webhook] Received message: "Hello"
[WhatsApp Webhook] From: +1234567890
```

### 3. Test in Meta Test Console
1. Go to **WhatsApp** → **API Setup**
2. Click **"Send test message"**
3. Check if your webhook receives it

---

## 🔐 Security Checklist

- [ ] HTTPS enabled on your domain (Meta requires HTTPS)
- [ ] `WHATSAPP_VERIFY_TOKEN` is a long, random string
- [ ] Webhook validates Meta's signature (for POST requests)
- [ ] Rate limiting configured
- [ ] Logs sensitive data redacted

---

## 📞 Webhook Events You'll Receive

### 1. Text Messages
```json
{
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "1234567890",
          "text": {"body": "Hello"},
          "type": "text"
        }]
      }
    }]
  }]
}
```

### 2. Voice Messages
```json
{
  "messages": [{
    "type": "audio",
    "audio": {
      "id": "media_id",
      "mime_type": "audio/ogg"
    }
  }]
}
```

### 3. Location Shares
```json
{
  "messages": [{
    "type": "location",
    "location": {
      "latitude": 37.7749,
      "longitude": -122.4194
    }
  }]
}
```

### 4. Button Clicks
```json
{
  "messages": [{
    "type": "button",
    "button": {
      "text": "Clock In",
      "payload": "CLOCK_IN_ACTION"
    }
  }]
}
```

### 5. Status Updates
```json
{
  "statuses": [{
    "id": "wamid.xxx",
    "status": "delivered",
    "timestamp": "1234567890"
  }]
}
```

---

## 🚨 Troubleshooting

### Webhook Verification Fails

**Problem**: Meta shows "Webhook verification failed"

**Solutions**:
1. Check HTTPS is working: `curl https://your-domain.com`
2. Verify token matches: Check `.env` file
3. Check Django logs for errors
4. Ensure no redirect on the URL
5. Test with curl command above

### Messages Not Received

**Problem**: Messages sent to WhatsApp, but webhook not triggered

**Solutions**:
1. Check Meta webhook subscription status
2. Verify phone number is connected
3. Check webhook fields are subscribed
4. Review Meta webhook logs (in developer console)
5. Test with Meta's test message feature

### Signature Validation Errors

**Problem**: Webhook receives messages but rejects them

**Solutions**:
1. Verify `WHATSAPP_ACCESS_TOKEN` is correct
2. Check timestamp tolerance (5 minutes)
3. Ensure raw request body is used for signature

---

## 📊 Monitoring

### Check Webhook Status in Meta
1. **WhatsApp** → **Configuration**
2. See **Webhook Status**: Should be ✅ Active
3. View **Recent Deliveries** to see success/failure rates

### Django Logs
```bash
# Watch webhook activity
tail -f /var/log/django/django.log | grep "WhatsApp"

# Count messages received today
grep "WhatsApp Webhook" /var/log/django/django.log | grep "$(date +%Y-%m-%d)" | wc -l
```

---

## ✅ Final Checklist

Before going live:

- [ ] Domain has valid SSL certificate
- [ ] Webhook URL is publicly accessible
- [ ] `WHATSAPP_VERIFY_TOKEN` set in `.env`
- [ ] Webhook verified in Meta console
- [ ] Subscribed to all required webhook fields
- [ ] Test message sent and received successfully
- [ ] Logs show successful webhook calls
- [ ] Error handling tested (invalid messages, etc.)

---

## 🎯 Quick Reference

| Item | Value |
|------|-------|
| **Webhook Path** | `/api/notifications/whatsapp/webhook/` |
| **Full URL Pattern** | `api/notifications/` prefix in `mizan/urls.py` |
| **View Function** | `notifications.views.whatsapp_webhook` |
| **HTTP Methods** | GET (verification), POST (messages) |
| **Required Headers** | None for GET, `X-Hub-Signature-256` for POST |
