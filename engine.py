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
    try:
        log_event("Iniciando processamento", {"phone": phone, "campaign_id": campaign_id, "message": message})

        # Tratamento de código de campanha
        if message.lower().startswith("começar "):
            code = message.split(" ")[1].upper()
            campaign = get_campaign_by_code(code)
            if not campaign:
                log_event("Código de campanha inválido", {"code": code, "phone": phone})
                return "Código de campanha inválido."
            campaign_id = campaign['campaign_id']
            save_user_state(phone, campaign_id, None, {})
            log_event("Campanha iniciada por código", {"code": code, "campaign_id": campaign_id, "phone": phone})
            message = "começar"

        campaign = get_campaign(campaign_id)
        if not campaign:
            log_event("Erro ao carregar campanha", {"campaign_id": campaign_id})
            return "Erro ao carregar campanha."

        flow = campaign.get("flow_json") or campaign.get("questions_json")
        if not flow or not isinstance(flow, dict):
            log_event("Flow/questions inválido", {"campaign_id": campaign_id, "flow": flow})
            return "Campanha inválida."

        questions = flow.get("questions", [])
        if not questions or not isinstance(questions, list):
            log_event("Campanha sem perguntas definidas", {"campaign_id": campaign_id, "questions": questions})
            return "Campanha sem perguntas definidas."

        # Validar estrutura das perguntas
        for q in questions:
            if not all(key in q for key in ["id", "text", "type"]):
                log_event("Pergunta inválida", {"campaign_id": campaign_id, "question": q})
                return "Campanha com perguntas mal configuradas."

        user_state = get_user_state(phone, campaign_id)
        current_step = user_state.get("current_step")
        answers = user_state.get("answers", {})

        # Início da campanha
        if not current_step or message.lower() in ["participar", "começar"]:
            next_question = questions[0]
            save_user_state(phone, campaign_id, next_question["id"], answers)
            log_event("Estado inicial salvo", {"phone": phone, "campaign_id": campaign_id, "step": next_question["id"]})
            return next_question["text"]

        # Encontrar a pergunta atual
        current_question = next((q for q in questions if str(q["id"]) == str(current_step)), None)
        if not current_question:
            log_event("Pergunta atual não encontrada", {"current_step": current_step, "phone": phone})
            return "Ocorreu um erro no processamento."

        # Processar resposta
        valid_answer = False
        option_idx = None
        
        if current_question["type"] == "quick_reply":
            if message.startswith("opt_"):
                try:
                    option_idx = int(message.split("_")[1])
                    if 0 <= option_idx < len(current_question.get("options", [])):
                        valid_answer = True
                except (ValueError, IndexError):
                    pass
                    
        elif current_question["type"] == "multiple_choice":
            valid_options = [chr(97 + i) for i in range(len(current_question.get("options", [])))]  # a, b, c, ...
            if message.lower() in valid_options:
                option_idx = valid_options.index(message.lower())
                valid_answer = True
                
        else:  # open_text ou outros tipos
            valid_answer = bool(message.strip())

        if not valid_answer:
            log_event("Resposta inválida", {
                "phone": phone,
                "campaign_id": campaign_id,
                "question_id": current_question["id"],
                "answer": message,
                "type": current_question["type"]
            })
            return current_question["text"]  # Reenviar a mesma pergunta

        # Salvar resposta válida
        answers[str(current_question["id"])] = message
        log_event("Resposta salva", {
            "phone": phone,
            "campaign_id": campaign_id,
            "question_id": current_question["id"],
            "answer": message
        })

        # Determinar próxima pergunta
        next_question = None
        
        # Verificar se há condição para pular para outra pergunta
        for q in questions:
            if q.get("condition") and str(q["condition"]).lower() == message.lower():
                next_question = q
                break

        # Se não houve match por condição, seguir o fluxo normal
        if not next_question:
            current_index = next((i for i, q in enumerate(questions) if str(q["id"]) == str(current_step)), None)
            if current_index is not None and current_index + 1 < len(questions):
                next_question = questions[current_index + 1]

        # Salvar estado e retornar próxima pergunta ou mensagem final
        if next_question:
            save_user_state(phone, campaign_id, next_question["id"], answers)
            log_event("Próximo estado salvo", {
                "phone": phone,
                "campaign_id": campaign_id,
                "step": next_question["id"]
            })
            return next_question["text"]
        else:
            save_user_state(phone, campaign_id, None, answers)
            log_event("Finalizando pesquisa", {
                "phone": phone,
                "campaign_id": campaign_id,
                "answers": answers
            })
            return flow.get("outro", "Obrigado por participar da pesquisa!")

    except Exception as e:
        log_event("Erro no processamento", {
            "error": str(e),
            "phone": phone,
            "campaign_id": campaign_id,
            "message": message
        })
        return "Ocorreu um erro ao processar sua mensagem. Por favor, tente novamente."