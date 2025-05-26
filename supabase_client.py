import os
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

def get_campaign(campaign_id):
    url = f"{SUPABASE_URL}/rest/v1/iap_campaigns?campaign_id=eq.{campaign_id}"
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200 and res.json():
        return res.json()[0]
    return None

def get_user_state(phone, campaign_id):
    url = f"{SUPABASE_URL}/rest/v1/whatsapp_user_states?phone=eq.{phone}&campaign_id=eq.{campaign_id}&limit=1"
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200 and res.json():
        return res.json()[0]
    return {"current_step": None, "answers": {}}

def save_user_state(phone, campaign_id, step, answers):
    url = f"{SUPABASE_URL}/rest/v1/whatsapp_user_states"
    payload = {
        "phone": phone,
        "campaign_id": campaign_id,
        "current_step": str(step),
        "answers": answers,
    }
    headers = HEADERS.copy()
    headers["Prefer"] = "resolution=merge-duplicates"
    res = requests.post(url, headers=headers, json=payload)
    return res.status_code in (200, 201)
