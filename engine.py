from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code
import logging
import json
import re
import traceback

def is_valid_cpf(cpf: str) -> bool:
    """Valida CPF, considerando tanto formatados (xxx.xxx.xxx-xx) quanto não formatados"""
    try:
        cpf = re.sub(r'[^0-9]', '', cpf)
        if len(cpf) != 11 or cpf == cpf[0] * 11:
            return False
        soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
        d1 = (soma * 10) % 11
        d1 = d1 if d1 < 10 else 0
        soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
        d2 = (soma * 10) % 11
        d2 = d2 if d2 < 10 else 0
        return cpf[-2:] == f"{d1}{d2}"
    except:
        return False

# Configuração de logs
logging.basicConfig(
    filename='/home/flow_engine/engine.log',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)
petition_logger = logging.getLogger('petition')
petition_handler = logging.FileHandler('/home/flow_engine/petition.log')
petition_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
petition_logger.addHandler(petition_handler)
petition_logger.setLevel(logging.INFO)

def log_event(message, data={}):
    log_entry = {'message': message, 'data': data}
    logging.info(json.dumps(log_entry))

def log_petition_event(message, data={}):
    log_entry = {'message': message, 'data': data}
    petition_logger.info(json.dumps(log_entry))

async def process_message(phone: str, campaign_id: str, message: str) -> dict:
    try:
        log_event("Iniciando processamento", {"phone": phone, "campaign_id": campaign_id, "message": message})

        if message.lower().startswith("começar "):
            code = message.split(" ")[1].upper()
            campaign = get_campaign_by_code(code)
            if not campaign:
                return {"next_message": "Código de campanha inválido."}
            campaign_id = campaign['campaign_id']
            log_event("Inicializando campanha", {"phone": phone, "campaign_id": campaign_id})
            save_user_state(phone, campaign_id, None, {})
            message = "começar"

        campaign = get_campaign(campaign_id)
        if not campaign:
            return {"next_message": "Erro ao carregar campanha."}

        flow = None
        flow_json = campaign.get("flow_json", {})
        questions_json = campaign.get("questions_json", {})
        if questions_json.get("questions"):
            flow = questions_json
        elif flow_json.get("questions"):
            flow = flow_json
        else:
            return {"next_message": "Campanha sem perguntas definidas."}

        questions = flow.get("questions", [])
        if not questions:
            return {"next_message": "Campanha sem perguntas válidas."}

        is_petition = flow.get("type") == "petition" or flow_json.get("is_petition", False)
        log_event("Tipo de campanha identificado", {"is_petition": is_petition})

        user_state = get_user_state(phone, campaign_id)
        current_step = user_state.get("current_step")
        answers = user_state.get("answers", {})

        log_event("Estado do usuário carregado", {"current_step": current_step, "answers": answers})

        if not current_step or message.lower() in ["participar", "começar", "assinar"]:
            next_question = questions[0]
            log_event("Iniciando pesquisa", {"question_id": next_question["id"], "question_text": next_question["text"]})
            save_user_state(phone, campaign_id, next_question["id"], answers)
            message_text = next_question["text"]
            if next_question["type"] in ["quick_reply", "multiple_choice"]:
                options = next_question.get("options", [])
                if options:
                    letters = [chr(97 + i) for i in range(len(options))]
                    message_text += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
            return {"next_message": message_text}

        current_question = next((q for q in questions if str(q["id"]) == str(current_step)), None)
        if not current_question and answers:
            ids_respondidas = sorted([int(k) for k in answers.keys()])
            ultima_id = ids_respondidas[-1]
            current_question = next((q for q in questions if int(q["id"]) == ultima_id), None)

        if not current_question:
            log_event("Erro: pergunta atual não encontrada", {"current_step": current_step, "answers": answers})
            return {"next_message": "Erro interno: pergunta atual não encontrada."}

        valid_answer = False
        confirmation_text = ""
        selected = ""
        options = current_question.get("options", [])

        log_event("Processando resposta", {"message": message, "question_id": current_question["id"], "question_text": current_question["text"]})

        if current_question["type"] in ["quick_reply", "multiple_choice"]:
            letters = [chr(97 + i) for i in range(len(options))]
            numbers = [str(i + 1) for i in range(len(options))]
            option_map = {opt.lower(): f"opt_{i}" for i, opt in enumerate(options)}
            message_lower = message.lower()

            if message.startswith("opt_"):
                try:
                    idx = int(message.split("_")[1])
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        valid_answer = True
                except:
                    pass
            elif message_lower in letters:
                try:
                    idx = letters.index(message_lower)
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        valid_answer = True
                except:
                    pass
            elif message in numbers:
                try:
                    idx = int(message) - 1
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        valid_answer = True
                except:
                    pass
            elif message_lower in option_map:
                message = option_map[message_lower]
                idx = int(message.split("_")[1])
                selected = options[idx]
                valid_answer = True

            if valid_answer:
                answers[str(current_question["id"])] = selected
                confirmation_text = f"✔️ Você escolheu: {selected}"

        elif current_question["type"] in ["text", "open_text"]:
            question_text = current_question.get("text", "").lower()
            response = message.strip()
            if response:
                if "cpf" in question_text or current_question.get("requires_cpf"):
                    if not is_valid_cpf(response):
                        log_event("CPF inválido detectado", {"cpf": response, "phone": phone})
                        return {
                            "next_message": "❌ CPF inválido. Por favor, digite um CPF válido com 11 dígitos (apenas números)."
                        }
                    cpf_limpo = re.sub(r'[^0-9]', '', response)
                    answers[str(current_question["id"])] = cpf_limpo
                    valid_answer = True
                    confirmation_text = f"✔️ CPF registrado: {cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-{cpf_limpo[9:]}"
                else:
                    answers[str(current_question["id"])] = response
                    valid_answer = True
                    confirmation_text = f"✔️ Resposta registrada: {response}"

        if not valid_answer:
            log_event("Resposta inválida", {
                "question_id": current_question["id"],
                "question_type": current_question["type"],
                "answer": message
            })
            message_text = f"❌ Resposta inválida. Escolha uma das opções abaixo:\n{current_question['text']}"
            if options:
                letters = [chr(97 + i) for i in range(len(options))]
                message_text += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
            return {"next_message": message_text}

        if int(current_question["id"]) > len(questions):
            log_event("Erro: question_id excede número de perguntas", {
                "question_id": current_question["id"],
                "total_questions": len(questions)
            })
            return {"next_message": "Erro interno: ID da pergunta inválido."}

        log_event("Salvando resposta", {"phone": phone, "campaign_id": campaign_id, "question_id": current_question["id"], "answer": selected or message.strip()})
        save_user_state(phone, campaign_id, current_question["id"], answers)
        log_event("Estado salvo", {"phone": phone, "campaign_id": campaign_id})

        # Seleção da próxima pergunta
        next_question = None
        current_index = next((i for i, q in enumerate(questions) if str(q["id"]) == str(current_question["id"])), -1)
        condition = selected.lower() if selected else None

        if valid_answer and current_index != -1:
            # Buscar a próxima pergunta com a mesma condição, na ordem dos IDs
            for q in sorted(questions[current_index + 1:], key=lambda x: int(x["id"])):
                if q.get("condition") and q.get("condition").lower() == condition:
                    next_question = q
                    log_event("Próxima pergunta selecionada", {"question_id": q["id"], "question_text": q["text"]})
                    break

        # Validação de respostas obrigatórias para petições
        if is_petition and valid_answer:
            required_questions = [q for q in questions if q.get("condition") and q.get("condition").lower() == condition and q["type"] != "text"]
            for q in required_questions:
                if str(q["id"]) not in answers:
                    next_question = q
                    log_event("Pergunta obrigatória não respondida encontrada", {"question_id": q["id"], "question_text": q["text"]})
                    break

        if next_question:
            log_event("Avançando para próxima pergunta", {"question_id": next_question["id"], "question_text": next_question["text"]})
            save_user_state(phone, campaign_id, next_question["id"], answers)
            message_text = f"{confirmation_text}\n\n{next_question['text']}"
            if next_question["type"] in ["quick_reply", "multiple_choice"]:
                options = next_question.get("options", [])
                if options:
                    letters = [chr(97 + i) for i in range(len(options))]
                    message_text += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
            return {"next_message": message_text}
        else:
            # Verificação final para petições
            if is_petition:
                required_questions = [q for q in questions if q.get("condition") and q.get("condition").lower() == condition and q["type"] != "text"]
                for q in required_questions:
                    if str(q["id"]) not in answers:
                        log_event("Faltam respostas obrigatórias", {"question_id": q["id"], "question_text": q["text"]})
                        save_user_state(phone, campaign_id, q["id"], answers)
                        message_text = f"Por favor, responda: {q['text']}"
                        if q["type"] in ["quick_reply", "multiple_choice"]:
                            options = q.get("options", [])
                            if options:
                                letters = [chr(97 + i) for i in range(len(options))]
                                message_text += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
                        return {"next_message": message_text}

            log_event("Finalizando pesquisa", {"phone": phone, "campaign_id": campaign_id, "answers": answers})
            log_petition_event("Finalizando petição", {"phone": phone, "campaign_id": campaign_id, "answers": answers})
            save_user_state(phone, campaign_id, None, answers)
            final_message = flow.get("outro", "Obrigado por participar da pesquisa!")
            if current_question.get("type") == "text" and current_question.get("message"):
                final_message = current_question["message"]
            return {"next_message": final_message}

    except Exception as e:
        log_event("Erro no processamento", {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "phone": phone,
            "message": message,
            "campaign_id": campaign_id
        })
        return {"next_message": "⚠️ Ocorreu um erro interno. Por favor, tente novamente ou digite outro CPF."}