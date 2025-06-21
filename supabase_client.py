import os
import requests
from dotenv import load_dotenv
import logging
import json
from typing import Dict, Any, Optional

# Configurar logging para supabase.log
logging.basicConfig(
    filename='supabase.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Configuração de log específico para petition.log
petition_logger = logging.getLogger('petition')
petition_handler = logging.FileHandler('/home/flow_engine/petition.log')
petition_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
petition_logger.addHandler(petition_handler)
petition_logger.setLevel(logging.INFO)

def log_event(message: str, data: Dict = {}):
    log_entry = {'message': message, 'data': data}
    logging.info(json.dumps(log_entry, ensure_ascii=False))

def log_petition_event(message: str, data: Dict = {}):
    log_entry = {'message': message, 'data': data}
    petition_logger.info(json.dumps(log_entry, ensure_ascii=False))

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    log_event("Erro: Variáveis de ambiente SUPABASE_URL ou SUPABASE_KEY não definidas")
    raise ValueError("Supabase URL ou Key não configurados")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

def get_campaign(campaign_id: str) -> Optional[Dict]:
    url = f"{SUPABASE_URL}/rest/v1/iap_campaigns?campaign_id=eq.{campaign_id}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200 and res.json():
            campaign = res.json()[0]
            log_event("Campanha carregada", {"campaign_id": campaign_id, "campaign": campaign})
            return campaign
        log_event("Campanha não encontrada", {"campaign_id": campaign_id, "status_code": res.status_code, "response": res.text})
        return None
    except Exception as e:
        log_event("Erro ao carregar campanha", {"campaign_id": campaign_id, "error": str(e)})
        return None

def get_campaign_by_code(code: str) -> Optional[Dict]:
    url = f"{SUPABASE_URL}/rest/v1/iap_campaign_codes?code=eq.{code}&select=campaign_id"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200 and res.json():
            campaign_id = res.json()[0]['campaign_id']
            log_event("Campanha encontrada por código", {"code": code, "campaign_id": campaign_id})
            return get_campaign(campaign_id)
        log_event("Código de campanha inválido", {"code": code, "status_code": res.status_code, "response": res.text})
        return None
    except Exception as e:
        log_event("Erro ao buscar campanha por código", {"code": code, "error": str(e)})
        return None

def get_user_state(phone: str, campaign_id: str) -> Dict:
    url = f"{SUPABASE_URL}/rest/v1/whatsapp_user_states?phone=eq.{phone}&campaign_id=eq.{campaign_id}&limit=1"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200 and res.json():
            state = res.json()[0]
            log_event("Estado do usuário carregado", {"phone": phone, "campaign_id": campaign_id, "state": state})
            return state
        log_event("Nenhum estado encontrado, retornando padrão", {"phone": phone, "campaign_id": campaign_id, "status_code": res.status_code, "response": res.text})
        return {"current_step": None, "answers": {}}
    except Exception as e:
        log_event("Erro ao carregar estado do usuário", {"phone": phone, "campaign_id": campaign_id, "error": str(e)})
        return {"current_step": None, "answers": {}}

def save_user_state(phone: str, campaign_id: str, step: Optional[str], answers: Dict) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/whatsapp_user_states"
    # Garantir que answers seja um dicionário serializável
    try:
        json.dumps(answers)
    except TypeError as e:
        log_event("Erro ao serializar answers", {"phone": phone, "campaign_id": campaign_id, "answers": str(answers), "error": str(e)})
        return False
    payload = {
        "phone": phone,
        "campaign_id": campaign_id,
        "current_step": str(step) if step else None,
        "answers": answers,
    }
    headers = HEADERS.copy()
    headers["Prefer"] = "resolution=merge-duplicates"
    params = {
        "on_conflict": "phone,campaign_id"
    }
    log_petition_event("Tentando salvar estado do usuário no Supabase", {
        "phone": phone,
        "campaign_id": campaign_id,
        "step": step,
        "answers": answers
    })
    try:
        res = requests.post(url, headers=headers, params=params, json=payload, timeout=10)
        success = res.status_code in (200, 201)
        log_event("Salvando estado do usuário", {
            "phone": phone,
            "campaign_id": campaign_id,
            "step": step,
            "answers": answers,
            "status_code": res.status_code,
            "response": res.text,
            "success": success
        })
        log_petition_event("Resultado do salvamento no Supabase", {
            "phone": phone,
            "campaign_id": campaign_id,
            "status_code": res.status_code,
            "response": res.text,
            "success": success
        })
        if not success:
            log_event("Falha ao salvar estado do usuário", {
                "phone": phone,
                "campaign_id": campaign_id,
                "status_code": res.status_code,
                "response": res.text
            })
        return success
    except Exception as e:
        log_event("Erro ao salvar estado do usuário", {
            "phone": phone,
            "campaign_id": campaign_id,
            "step": step,
            "answers": answers,
            "error": str(e)
        })
        return False