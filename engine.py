from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code
import logging
import json

# Configurar logging estruturado
logging.basicConfig(
    filename='/home/flow_engine/engine.log',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)

def log_event(message, data={}):
    log_entry = {'message': message, 'data': data}
    logging.info(json.dumps(log_entry))

async def process_message(phone: str, campaign_id: str, message: str) -> str:
    log_event("Iniciando processamento", {"phone": phone, "campaign_id": campaign_id, "message": message})

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

    # Tentar flow_json, depois questions_json
    flow = campaign.get("flow_json") or campaign.get("questions_json")
    log_event("Conteúdo de flow/questions", {"flow": flow})

    if not flow or not isinstance(flow, dict):
        log_event("Flow/questions inválido", {"campaign_id": campaign_id, "flow": flow})
        return "Campanha inválida."

    questions = flow.get("questions", [])
    if not questions or not isinstance(questions, list):
        log_event("Campanha sem perguntas definidas", {"campaign_id": campaign_id, "questions": questions})
        return "Campanha sem perguntas definidas."

    for q in questions:
        if not all(key in q for key in ["id", "text"]):
            log_event("Pergunta inválida", {"campaign_id": campaign_id, "question": q})
            return "Campanha com perguntas mal configuradas."

    user_state = get_user_state(phone, campaign_id)
    current_step = user_state.get("current_step")
    answers = user_state.get("answers", {})

    next_question = None

    if not current_step or message.lower() in ["participar", "começar"]:
        next_question = questions[0]
        save_user_state(phone, campaign_id, next_question["id"], answers)  # Salvar estado inicial
        log_event("Estado inicial salvo", {"phone": phone, "campaign_id": campaign_id, "step": next_question["id"]})
    else:
        last_q = next((q for q in questions if str(q["id"]) == str(current_step)), None)
        if last_q:
            # Validar resposta
            valid_answer = False
            option_idx = None
            if last_q["type"] == "quick_reply":
                if message.startswith("opt_"):
                    try:
                        option_idx = int(message.replace("opt_", ""))
                        if 0 <= option_idx < len(last_q["options"]):
                            valid_answer = True
                    except ValueError:
                        pass
            elif last_q["type"] == "multiple_choice":
                valid_options = [chr(65 + i).lower() for i in range(len(last_q["options"]))]
                if message.lower() in valid_options:
                    option_idx = valid_options.index(message.lower())
                    valid_answer = True
            else:  # open_text ou outros
                valid_answer = bool(message.strip())

            if valid_answer:
                answers[str(last_q["id"])] = message
                save_user_state(phone, campaign_id, last_q["id"], answers)
                log_event("Resposta salva", {"phone": phone, "campaign_id": campaign_id, "question_id": last_q["id"], "answer": message})
                condition_match = None
                for q in questions:
                    if q.get("condition") and q.get("condition").lower() == message.lower():
                        condition_match = q
                        break
                if condition_match:
                    next_question = condition_match
                else:
                    current_idx = questions.index(last_q)
                    if current_idx + 1 < len(questions):
                        next_question = questions[current_idx + 1]
            else:
                log_event("Resposta inválida", {"phone": phone, "campaign_id": campaign_id, "question_id": last_q["id"], "answer": message})
                return last_q["text"]  # Reenviar mesma pergunta

    if not next_question:
        save_user_state(phone, campaign_id, current_step, answers)
        log_event("Finalizando pesquisa", {"phone": phone, "campaign_id": campaign_id, "answers": answers})
        return flow.get("outro", "Obrigado por participar da pesquisa!")

    save_user_state(phone, campaign_id, next_question["id"], answers)
    log_event("Enviando próxima pergunta", {
        "phone": phone,
        "campaign_id": campaign_id,
        "question_id": next_question["id"],
        "question_text": next_question["text"]
    })
    return next_question["text"]