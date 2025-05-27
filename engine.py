from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code
import logging
import json

# Configurar logging estruturado
logging.basicConfig(
    filename='engine.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def log_event(message, data={}):
    log_entry = {'message': message, 'data': data}
    logging.info(json.dumps(log_entry))

async def process_message(phone: str, campaign_id: str, message: str) -> str:
    if message.startswith("começar "):
        code = message.split(" ")[1].upper()
        campaign = get_campaign_by_code(code)
        if not campaign:
            log_event("Código de campanha inválido", {"code": code, "phone": phone})
            return "Código de campanha inválido."
        campaign_id = campaign['campaign_id']
        save_user_state(phone, campaign_id, None, {})  # Resetar estado
        log_event("Campanha iniciada por código", {"code": code, "campaign_id": campaign_id, "phone": phone})
        message = "começar"

    campaign = get_campaign(campaign_id)
    log_event("Campanha recebida", {"campaign_id": campaign_id, "campaign": campaign})

    if not campaign:
        log_event("Erro ao carregar campanha", {"campaign_id": campaign_id})
        return "Erro ao carregar campanha."

    flow = campaign.get("flow_json")
    log_event("Conteúdo de flow_json", {"flow": flow})

    if not flow or not isinstance(flow, dict) or "questions" not in flow:
        log_event("Campanha sem perguntas definidas", {"campaign_id": campaign_id})
        return "Campanha sem perguntas definidas."

    questions = flow["questions"]
    user_state = get_user_state(phone, campaign_id)

    current_step = user_state.get("current_step")
    answers = user_state.get("answers", {})

    next_question = None

    if not current_step:
        next_question = questions[0]
    else:
        last_q = next((q for q in questions if str(q["id"]) == str(current_step)), None)
        if last_q:
            answers[str(last_q["id"])] = message
            condition_match = None
            for q in questions:
                if q.get("condition") and q.get("condition") == message:
                    condition_match = q
                    break
            if condition_match:
                next_question = condition_match
            else:
                current_idx = questions.index(last_q)
                if current_idx + 1 < len(questions):
                    next_question = questions[current_idx + 1]

    if not next_question:
        save_user_state(phone, campaign_id, current_step, answers)
        log_event("Finalizando pesquisa", {"phone": phone, "campaign_id": campaign_id, "answers": answers})
        return "Obrigado por participar da pesquisa!"

    save_user_state(phone, campaign_id, next_question["id"], answers)
    log_event("Enviando próxima pergunta", {
        "phone": phone,
        "campaign_id": campaign_id,
        "question_id": next_question["id"],
        "question_text": next_question["text"]
    })
    return next_question["text"]