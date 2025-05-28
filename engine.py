from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code
import logging
import json

# Configurar logging
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

async def process_message(phone: str, campaign_id: str, message: str) -> dict:
    try:
        log_event("Iniciando processamento", {"phone": phone, "campaign_id": campaign_id, "message": message})

        # Tratamento de código de campanha
        if message.lower().startswith("começar "):
            code = message.split(" ")[1].upper()
            campaign = get_campaign_by_code(code)
            if not campaign:
                return {"next_message": "Código de campanha inválido."}
            campaign_id = campaign['campaign_id']
            save_user_state(phone, campaign_id, None, {})
            message = "começar"

        campaign = get_campaign(campaign_id)
        if not campaign:
            return {"next_message": "Erro ao carregar campanha."}

        flow = campaign.get("flow_json") or campaign.get("questions_json")
        if not flow or not isinstance(flow, dict):
            return {"next_message": "Campanha inválida."}

        questions = flow.get("questions", [])
        if not questions:
            return {"next_message": "Campanha sem perguntas definidas."}

        user_state = get_user_state(phone, campaign_id)
        current_step = user_state.get("current_step")
        answers = user_state.get("answers", {})

        # Início da campanha
        if not current_step or message.lower() in ["participar", "começar"]:
            next_question = questions[0]
            save_user_state(phone, campaign_id, next_question["id"], answers)
            return {"next_message": next_question["text"]}

        # Encontrar pergunta atual
        current_question = next((q for q in questions if str(q["id"]) == str(current_step)), None)
        if not current_question:
            return {"next_message": "Ocorreu um erro no processamento."}

        # Processar resposta
        valid_answer = False
        
        if current_question["type"] == "quick_reply":
            if message.startswith("opt_"):
                try:
                    option_idx = int(message.split("_")[1])
                    if 0 <= option_idx < len(current_question.get("options", [])):
                        valid_answer = True
                        # Salvar o texto da opção, não o opt_X
                        answers[str(current_question["id"])] = current_question["options"][option_idx]
                except (ValueError, IndexError):
                    pass
                    
        elif current_question["type"] == "multiple_choice":
            options = current_question.get("options", [])
            valid_options = [chr(97 + i) for i in range(len(options))]  # a, b, c, ...
            if message.lower() in valid_options:
                option_idx = valid_options.index(message.lower())
                answers[str(current_question["id"])] = options[option_idx]
                valid_answer = True
                
        else:  # open_text ou outros
            if message.strip():
                answers[str(current_question["id"])] = message.strip()
                valid_answer = True

        if not valid_answer:
            log_event("Resposta inválida", {
                "question_id": current_question["id"],
                "question_type": current_question["type"],
                "answer": message
            })
            return {"next_message": current_question["text"]}

        # Determinar próxima pergunta
        next_question = None
        
        # Verificar condições primeiro
        for q in questions:
            if q.get("condition") and str(q["condition"]).lower() == answers[str(current_question["id"])].lower():
                next_question = q
                break

        # Se não houve match por condição, seguir o fluxo normal
        if not next_question:
            current_index = next((i for i, q in enumerate(questions) if str(q["id"]) == str(current_step)), -1)
            if current_index != -1 and current_index + 1 < len(questions):
                next_question = questions[current_index + 1]

        # Salvar estado e retornar próxima pergunta ou mensagem final
        if next_question:
            save_user_state(phone, campaign_id, next_question["id"], answers)
            return {"next_message": next_question["text"]}
        else:
            save_user_state(phone, campaign_id, None, answers)
            return {"next_message": flow.get("outro", "Obrigado por participar da pesquisa!")}

    except Exception as e:
        log_event("Erro no processamento", {"error": str(e)})
        return {"next_message": "Ocorreu um erro ao processar sua mensagem."}