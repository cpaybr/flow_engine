from fastapi import FastAPI, Request
from pydantic import BaseModel
from engine import process_message
import logging
import json

# Configurar logging estruturado
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def log_event(message, data={}):
    log_entry = {'message': message, 'data': data}
    logging.info(json.dumps(log_entry))

app = FastAPI()

class ProcessRequest(BaseModel):
    phone: str
    campaign_id: str
    message: str

@app.post("/process")
async def process(request: Request):
    try:
        body = await request.json()
        phone = body.get("phone")
        campaign_id = body.get("campaign_id")
        message = body.get("message")
        if not all([phone, campaign_id, message]):
            log_event("Parâmetros inválidos no corpo da requisição", body)
            return {"detail": "Parâmetros obrigatórios ausentes"}
        response = await process_message(phone, campaign_id, message)
        log_event("Mensagem processada com sucesso", {"phone": phone, "campaign_id": campaign_id, "response": response})
        return {"next_message": response}
    except Exception as e:
        log_event("Erro ao processar requisição", {"error": str(e)})
        return {"detail": f"Erro ao interpretar corpo da requisição: {str(e)}"}