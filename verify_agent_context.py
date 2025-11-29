import requests
import os
import sys

# Configuration
BASE_URL = "http://localhost:8000"
LOGIN_URL = f"{BASE_URL}/api/auth/login/"
AGENT_CONTEXT_URL = f"{BASE_URL}/api/auth/agent-context/"

# Test Credentials (replace with valid ones if known, or use the ones I can find/create)
# I'll try to use a known user or create one if I could, but for now I'll use placeholders
# and expect the user to run this with valid credentials or I'll try to find one.
# Actually, I can use the `create_test_user.py` script logic if needed, but let's assume
# I can just try to login with a standard admin/test account.
EMAIL = "test@example.com"
PASSWORD = "test123"

def test_agent_context():
    print(f"Testing Agent Context Endpoint...")
    
    # 1. Login to get token
    print(f"Logging in as {EMAIL}...")
    try:
        response = requests.post(LOGIN_URL, json={"email": EMAIL, "password": PASSWORD})
        if response.status_code != 200:
            print(f"Login failed: {response.status_code} - {response.text}")
            # Try to create a user if login fails? No, that might be too invasive.
            # I'll just report failure.
            return
        
        tokens = response.json().get("tokens")
        access_token = tokens.get("access")
        print(f"Login successful. Token: {access_token[:10]}...")
        
    except Exception as e:
        print(f"Login error: {e}")
        return

    # 2. Call Agent Context Endpoint
    print(f"Calling {AGENT_CONTEXT_URL}...")
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(AGENT_CONTEXT_URL, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            print("✅ Success! Context retrieved:")
            print(f"User: {data['user']['email']} ({data['user']['role']})")
            print(f"Restaurant: {data['restaurant']['name']} ({data['restaurant']['id']})")
        else:
            print(f"❌ Failed: {response.status_code} - {response.text}")
            
    except Exception as e:
        print(f"Request error: {e}")

if __name__ == "__main__":
    # Allow passing credentials via args
    if len(sys.argv) > 2:
        EMAIL = sys.argv[1]
        PASSWORD = sys.argv[2]
    
    test_agent_context()
