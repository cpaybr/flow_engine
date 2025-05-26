from fastapi import FastAPI, Request
from pydantic import BaseModel
from engine import process_message

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
        response = await process_message(phone, campaign_id, message)
        return {"next_message": response}
    except Exception as e:
        return {"detail": f"Erro ao interpretar corpo da requisição: {str(e)}"}
