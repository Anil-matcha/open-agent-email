"""
Example: Autonomous AI Agent listening to its own Open Agent Email inbox in real-time.
No public IP or ngrok required - operates over WebSocket!
"""

import asyncio
import json
import websockets
import httpx

AGENT_EMAIL = "support@agents.local"
API_BASE = "http://127.0.0.1:8000"
WS_URL = f"ws://127.0.0.1:8000/v1/agents/{AGENT_EMAIL}/ws"

async def handle_inbound_email(client: httpx.AsyncClient, email_data: dict):
    print(f"\n📩 [NEW EMAIL RECEIVED]")
    print(f"From:    {email_data.get('sender')}")
    print(f"Subject: {email_data.get('subject')}")
    print(f"Message ID: {email_data.get('id')}")
    print(f"Thread:  {email_data.get('thread_id')}")

    # Fetch full email body
    msg_id = email_data.get("id")
    resp = await client.get(f"{API_BASE}/v1/agents/{AGENT_EMAIL}/messages/{msg_id}")
    if resp.status_code == 200:
        full_msg = resp.json()
        print(f"Body:    {full_msg.get('body_text')}")
        print(f"SPF/DKIM: SPF={full_msg.get('spf_status')}, DKIM={full_msg.get('dkim_status')}")

        # Simulate agent generating a response
        print("🤖 Generating AI reply...")
        reply_payload = {
            "body_text": f"Thank you for contacting Support. I have received your message regarding '{full_msg.get('subject')}'. Our team is on it!"
        }
        reply_resp = await client.post(
            f"{API_BASE}/v1/agents/{AGENT_EMAIL}/messages/{msg_id}/reply",
            json=reply_payload
        )
        if reply_resp.status_code == 201:
            print("✅ Sent in-thread reply successfully!")
        else:
            print(f"❌ Failed to reply: {reply_resp.text}")

async def listen_inbox():
    print(f"Connecting to real-time inbox stream for {AGENT_EMAIL}...")
    async with httpx.AsyncClient() as client:
        while True:
            try:
                async with websockets.connect(WS_URL) as ws:
                    print(f"🟢 Connected to {WS_URL}. Waiting for incoming mail...")
                    while True:
                        raw = await ws.recv()
                        msg = json.loads(raw)
                        event_type = msg.get("type")
                        data = msg.get("data", {})

                        if event_type == "email.received":
                            await handle_inbound_email(client, data)
                        elif event_type == "email.sent":
                            print(f"📤 Outbound sent confirmed: {data.get('subject')}")
            except Exception as e:
                print(f"Connection dropped ({e}). Reconnecting in 3 seconds...")
                await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(listen_inbox())
