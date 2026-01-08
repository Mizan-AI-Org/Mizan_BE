
import os
import sys
import django
from unittest.mock import MagicMock, patch

# Setup Django
sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from notifications.services import notification_service
from notifications.views_agent import send_whatsapp_from_agent
from rest_framework.test import APIRequestFactory

def test_backend_delegate_to_agent():
    print("Testing Backend -> Agent delegation...")
    with patch('requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"eventId": "evt_123"}
        
        ok, info = notification_service.send_lua_staff_invite(
            invitation_token="abc-123",
            phone="+1234567890",
            first_name="John",
            restaurant_name="Tasty Bites",
            invite_link="http://example.com/invite"
        )
        
        if ok and info['eventId'] == 'evt_123':
            print("✅ Backend correctly sent delegation request to Agent.")
            # Verify payload
            args, kwargs = mock_post.call_args
            body = kwargs['json']
            if body['eventType'] == 'staff_invite' and body['details']['phone'] == '+1234567890':
                 print("✅ Payload looks correct.")
            else:
                 print("❌ Payload incorrect:", body)
        else:
            print("❌ Backend failed to delegate.")

def test_agent_callback_to_backend():
    print("\nTesting Agent -> Backend callback...")
    factory = APIRequestFactory()
    
    # Mocking settings to ensure key validation works
    from django.conf import settings
    # Assuming LUA_WEBHOOK_API_KEY is set or we mock it. 
    # For this test, let's assume we can mock the view's access to settings
    
    with patch('notifications.views_agent.getattr') as mock_getattr:
        # Mocking settings.LUA_WEBHOOK_API_KEY
        def side_effect(obj, name, default=None):
            if name == 'LUA_WEBHOOK_API_KEY':
                return 'secret_agent_key'
            return default
        mock_getattr.side_effect = side_effect
        
        # Mocking notification_service.send_whatsapp_text
        with patch('notifications.services.notification_service.send_whatsapp_text') as mock_send:
             mock_send.return_value = (True, {'wamid': 'msg_123'})
             
             data = {
                 'phone': '+1234567890',
                 'type': 'text',
                 'body': 'Hello from Agent'
             }
             request = factory.post(
                 '/notifications/agent/send-whatsapp/', 
                 data, 
                 format='json',
                 headers={'Authorization': 'Bearer secret_agent_key'}
             )
             
             response = send_whatsapp_from_agent(request)
             
             if response.status_code == 200 and response.data['success']:
                 print("✅ Agent endpoint correctly processed request.")
                 mock_send.assert_called_with('+1234567890', 'Hello from Agent')
             else:
                 print("❌ Agent endpoint failed:", response.data)

if __name__ == "__main__":
    test_backend_delegate_to_agent()
    test_agent_callback_to_backend()
