import requests
import json
import os

# Configuration
API_BASE = "http://localhost:8000"
AGENT_KEY = "test-agent-key" # In dev, this might be LUA_WEBHOOK_API_KEY
RESTAURANT_ID = "c1234bab-fcdf-4a0f-966e-d09d1971e04f" # Example from lua.skill.yaml

def test_ingest_request():
    url = f"{API_BASE}/api/staff/agent/ingest-request/"
    headers = {
        "Authorization": f"Bearer {AGENT_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "restaurant_id": RESTAURANT_ID,
        "subject": "Work Certificate Request (Test)",
        "description": "I need a work certificate for my bank application. Can you please provide it?",
        "category": "DOCUMENT",
        "priority": "HIGH",
        "phone": "+1234567890",
        "metadata": {
            "source": "test_script"
        }
    }
    
    print(f"Sending request to {url}...")
    try:
        response = requests.post(url, headers=headers, json=payload)
        print(f"Status: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 201:
            request_id = response.json().get('id')
            print(f"Successfully created request: {request_id}")
            return request_id
    except Exception as e:
        print(f"Error: {e}")
    return None

def check_notifications(request_id):
    # In a real test, we'd check the DB or the /api/notifications/ list
    # For now, we'll verify via DB if possible or just assume success if 201
    pass

if __name__ == "__main__":
    req_id = test_ingest_request()
    if req_id:
        print("Test PASSED: Request created.")
    else:
        print("Test FAILED: Could not create request.")
