# main.py (atualizado)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from engine import process_message
from supabase_client import get_campaign
import uvicorn
import logging

app = FastAPI()

logging.basicConfig(filename="whatsapp.log", level=logging.INFO)

@app.post("/webhook")
async def whatsapp_webhook(request: Request):
    payload = await request.json()
    logging.info("Payload recebido: %s", payload)

    try:
        entry = payload.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        phone_number_id = value.get("metadata", {}).get("phone_number_id")

        if messages:
            message = messages[0]
            sender = message.get("from")
            text = message.get("text", {}).get("body", "")
            button = message.get("button", {}).get("text") or \
                     message.get("interactive", {}).get("button_reply", {}).get("title")
            campaign_id = value.get("metadata", {}).get("campaign_id") or None

            # Preferência por botão se houver
            user_input = button or text

            if not campaign_id:
                # fallback se você quiser identificar campanha pelo número, etc
                campaign = get_campaign_by_phone_number_id(phone_number_id)
                campaign_id = campaign.get("campaign_id") if campaign else None

            if not campaign_id:
                return JSONResponse(status_code=200, content={"message": "Campanha não encontrada"})

            reply = await process_message(sender, campaign_id, user_input)
            return JSONResponse(status_code=200, content={"reply": reply})

    except Exception as e:
        logging.error("Erro ao processar webhook: %s", str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})

    return JSONResponse(status_code=200, content={"message": "Ignorado"})

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)