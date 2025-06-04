from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code
import logging
import json

# Configuração de logs
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

        user_state = get_user_state(phone, campaign_id)
        current_step = user_state.get("current_step")
        answers = user_state.get("answers", {})

        log_event("Estado do usuário carregado", {"current_step": current_step, "answers": answers})

        if not current_step or message.lower() in ["participar", "começar", "assinar"]:
            next_question = questions[0]
            save_user_state(phone, campaign_id, next_question["id"], answers)
            log_event("Início de pesquisa", {"next_question": next_question["text"]})
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
            return {"next_message": "Erro interno: pergunta atual não encontrada."}

        valid_answer = False
        confirmation_text = ""
        selected = ""
        options = current_question.get("options", [])

        log_event("Processando resposta", {"message": message, "question": current_question})

        if current_question["type"] in ["quick_reply", "multiple_choice"]:
            letters = [chr(97 + i) for i in range(len(options))]
            numbers = [str(i + 1) for i in range(len(options))]
            option_map = {opt.lower(): f"opt_{i}" for i, opt in enumerate(options)}  # Mapear texto da opção pra opt_X

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
            if message.strip():
                answers[str(current_question["id"])] = message.strip()
                valid_answer = True
                confirmation_text = f"✔️ Resposta registrada: {message.strip()}"

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

        # Valida se o question_id está correto
        if int(current_question["id"]) > len(questions):
            log_event("Erro: question_id excede número de perguntas", {
                "question_id": current_question["id"],
                "total_questions": len(questions)
            })
            return {"next_message": "Erro interno: ID da pergunta inválido."}

        save_user_state(phone, campaign_id, current_question["id"], answers)
        log_event("Resposta salva", {"phone": phone, "campaign_id": campaign_id, "question_id": current_question["id"], "answer": selected or message.strip()})

        next_question = None
        current_index = next((i for i, q in enumerate(questions) if str(q["id"]) == str(current_question["id"])), -1)

        # Verifica perguntas com condição que correspondem à resposta atual
        if valid_answer and current_index != -1:
            for q in questions[current_index + 1:]:
                if q.get("condition") and str(q["condition"]).lower() == (selected.lower() if selected else message.strip().lower()):
                    next_question = q
                    break


        # 🔒 Verifica se não há próximas perguntas válidas com base na condição
        if not next_question and valid_answer and current_index != -1:
            remaining_questions = questions[current_index + 1:]
            conditioned_questions = [q for q in remaining_questions if q.get("condition")]
            if conditioned_questions:
                # Se todas as próximas exigem condição e nenhuma foi satisfeita, encerra
                match_found = False
                for q in conditioned_questions:
                    if q["condition"].lower() == (selected.lower() if selected else message.strip().lower()):
                        match_found = True
                        break
                if not match_found:
                    save_user_state(phone, campaign_id, None, answers)
                    final_message = flow.get("outro", "Obrigado por participar da pesquisa!")
                    return {"next_message": f"{confirmation_text}\n\n{final_message}"}

        # Se não houver pergunta condicional, pega a próxima pergunta na ordem
        if not next_question and current_index != -1:
            for i in range(current_index + 1, len(questions)):
                if not questions[i].get("condition"):  # Só pega perguntas sem condição
                    next_question = questions[i]
                    break

        log_event("Determinado próximo passo", {
            "de": current_question["id"],
            "para": next_question["id"] if next_question else "fim"
        })

        if next_question:
            save_user_state(phone, campaign_id, next_question["id"], answers)
            log_event("Atualizado current_question_id", {"new_id": next_question["id"]})
            message_text = f"{confirmation_text}\n\n{next_question['text']}"
            if next_question["type"] in ["quick_reply", "multiple_choice"]:
                options = next_question.get("options", [])
                if options:
                    letters = [chr(97 + i) for i in range(len(options))]
                    message_text += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
            return {"next_message": message_text}
        else:
            save_user_state(phone, campaign_id, None, answers)
            # Usar o message da pergunta final se for tipo text, senão o outro
            final_message = flow.get("outro", "Obrigado por participar da pesquisa!")
            if current_question.get("type") == "text" and current_question.get("message"):
                final_message = current_question["message"]
            return {"next_message": f"{confirmation_text}\n\n{final_message}"}

    except Exception as e:
        log_event("Erro no processamento", {"error": str(e)})
        return {"next_message": "Ocorreu um erro ao processar sua mensagem."}