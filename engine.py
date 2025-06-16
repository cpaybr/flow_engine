from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code
import logging
import json
import re
import traceback

def is_valid_cpf(cpf: str) -> bool:
    """Valida CPF, considerando tanto formatados (xxx.xxx.xxx-xx) quanto não formatados"""
    try:
        cpf = re.sub(r'[^0-9]', '', cpf)  # Remove tudo que não é dígito
        
        # Verifica se tem 11 dígitos e não é uma sequência de dígitos repetidos
        if len(cpf) != 11 or cpf == cpf[0] * 11:
            return False
            
        # Calcula o primeiro dígito verificador
        soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
        d1 = (soma * 10) % 11
        d1 = d1 if d1 < 10 else 0
        
        # Calcula o segundo dígito verificador
        soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
        d2 = (soma * 10) % 11
        d2 = d2 if d2 < 10 else 0
        
        # Verifica se os dígitos calculados conferem com os informados
        return cpf[-2:] == f"{d1}{d2}"
    except:
        return False

# Configuração de logs para engine.log
logging.basicConfig(
    filename='/home/flow_engine/engine.log',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)

# Configuração de log específico para petition.log
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
            log_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": None, "answers": {}})
            log_petition_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": None, "answers": {}})
            save_user_state(phone, campaign_id, None, {})
            log_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
            log_petition_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
            message = "começar"

        campaign = get_campaign(campaign_id)
        if not campaign:
            return {"next_message": "Erro ao carregar campanha."}

        # Priorizar questions_json se existir, ou se flow_json for nulo/vazio
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
            log_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": next_question["id"], "answers": answers})
            log_petition_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": next_question["id"], "answers": answers})
            save_user_state(phone, campaign_id, next_question["id"], answers)
            log_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
            log_petition_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
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

            if message.startswith("opt_"):
                try:
                    idx = int(message.split("_")[1])
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        valid_answer = True
                except:
                    pass
            elif message.lower() in letters:
                try:
                    idx = letters.index(message.lower())
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
            elif message.lower() in option_map:
                message = option_map[message.lower()]
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

        log_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": current_question["id"], "answers": answers})
        log_petition_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": current_question["id"], "answers": answers})
        save_user_state(phone, campaign_id, current_question["id"], answers)
        log_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
        log_petition_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
        log_event("Resposta salva", {"phone": phone, "campaign_id": campaign_id, "question_id": current_question["id"], "answer": selected or message.strip()})

        next_question = None
        current_index = next((i for i, q in enumerate(questions) if str(q["id"]) == str(current_question["id"])), -1)

        # Buscar a próxima pergunta com base na condição
        if valid_answer and current_index != -1:
            condition = selected.lower() if selected else None
            for q in questions[current_index + 1:]:
                if not q.get("condition") or (condition and q.get("condition").lower() == condition):
                    next_question = q
                    break

        # Validação de respostas obrigatórias para petições
        if is_petition and not next_question and valid_answer:
            condition = selected.lower() if selected else None
            required_questions = [q for q in questions if q.get("condition") and q.get("condition").lower() == condition and q["type"] != "text"]
            for q in required_questions:
                if str(q["id"]) not in answers:
                    next_question = q
                    log_event("Pergunta obrigatória não respondida encontrada", {"question_id": q["id"], "question_text": q["text"]})
                    break

        log_event("Determinado próximo passo", {
            "de": current_question["id"],
            "para": next_question["id"] if next_question else "fim"
        })

        if next_question:
            log_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": next_question["id"], "answers": answers})
            log_petition_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": next_question["id"], "answers": answers})
            save_user_state(phone, campaign_id, next_question["id"], answers)
            log_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
            log_petition_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
            log_event("Atualizado current_question_id", {"new_id": next_question["id"]})
            message_text = f"{confirmation_text}\n\n{next_question['text']}"
            if next_question["type"] in ["quick_reply", "multiple_choice"]:
                options = next_question.get("options", [])
                if options:
                    letters = [chr(97 + i) for i in range(len(options))]
                    message_text += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
            return {"next_message": message_text}
        else:
            # Antes de finalizar, verificar novamente respostas obrigatórias para petições
            if is_petition:
                condition = selected.lower() if selected else None
                required_questions = [q for q in questions if q.get("condition") and q.get("condition").lower() == condition and q["type"] != "text"]
                for q in required_questions:
                    if str(q["id"]) not in answers:
                        log_event("Faltam respostas obrigatórias", {"question_id": q["id"], "question_text": q["text"]})
                        return {"next_message": f"Por favor, responda: {q['text']}"}

            log_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": None, "answers": answers})
            log_petition_event("Chamando save_user_state", {"phone": phone, "campaign_id": campaign_id, "question_id": None, "answers": answers})
            save_user_state(phone, campaign_id, None, answers)
            log_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
            log_petition_event("save_user_state executado", {"phone": phone, "campaign_id": campaign_id})
            final_message = flow.get("outro", "Obrigado por participar da pesquisa!")
            if current_question.get("type") == "text" and current_question.get("message"):
                final_message = current_question["message"]
            log_event("Pesquisa finalizada", {"phone": phone, "campaign_id": campaign_id, "answers": answers})
            log_petition_event("Petição finalizada", {"phone": phone, "campaign_id": campaign_id, "answers": answers})
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