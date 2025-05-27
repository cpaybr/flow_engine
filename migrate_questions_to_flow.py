import requests
from supabase_client import SUPABASE_URL, SUPABASE_KEY, HEADERS
import logging
import json

# Configurar logging
logging.basicConfig(
    filename='migration.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def log_event(message, data={}):
    log_entry = {'message': message, 'data': data}
    logging.info(json.dumps(log_entry))

def supabase_request(method, endpoint, data=None):
    url = f"{SUPABASE_URL}/rest/v1{endpoint}"
    headers = HEADERS.copy()
    if method == 'PATCH':
        headers['Prefer'] = 'return=representation'
    try:
        if method == 'GET':
            res = requests.get(url, headers=headers)
        elif method == 'PATCH':
            res = requests.patch(url, headers=headers, json=data)
        if res.status_code in (200, 201) and res.json():
            log_event(f"Requisição {method} bem-sucedida", {'endpoint': endpoint, 'response': res.json()})
            return res.json()
        log_event(f"Erro na requisição {method}", {'endpoint': endpoint, 'status_code': res.status_code})
        return None
    except Exception as e:
        log_event(f"Exceção na requisição {method}", {'endpoint': endpoint, 'error': str(e)})
        return None

def migrate_questions_to_flow():
    campaigns = supabase_request('GET', '/iap_campaigns?select=campaign_id,questions_json,flow_json')
    if not campaigns:
        log_event("Nenhuma campanha encontrada para migração")
        return
    
    for campaign in campaigns:
        if not campaign.get('flow_json') and campaign.get('questions_json'):
            flow_json = {
                'questions': campaign['questions_json']['questions'],
                'intro': campaign['questions_json']['intro'] or '',
                'outro': campaign['questions_json']['outro'] or '',
                'type': campaign['questions_json']['type'] or 'survey'
            }
            result = supabase_request('PATCH', f"/iap_campaigns?campaign_id=eq.{campaign['campaign_id']}", {'flow_json': flow_json})
            if result:
                log_event("Campanha migrada com sucesso", {'campaign_id': campaign['campaign_id']})
            else:
                log_event("Erro ao migrar campanha", {'campaign_id': campaign['campaign_id']})

if __name__ == "__main__":
    migrate_questions_to_flow()