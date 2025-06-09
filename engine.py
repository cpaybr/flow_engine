from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code
import logging
import json
import re
import requests

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

def validate_cpf(cpf):
    """Valida um CPF usando o algoritmo de dígitos verificadores."""
    cpf = re.sub(r'\D', '', cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    try:
        for i in range(9, 11):
            value = sum(int(cpf[num]) * (i + 1 - num) for num in range(i))
            digit = (value * 10 % 11) % 10
            if digit != int(cpf[i]):
                return False
        return True
    except:
        return False

def get_integration_data(integration_id):
    """Busca dados da integração no Supabase."""
    try:
        response = supabase.table('iap_integration_configurations').select('config_data').eq('id', integration_id).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]['config_data']
        return None
    except Exception as e:
        log_event("Erro ao buscar integração", {"error": str(e), "integration_id": integration_id})
        return None

def send_whatsapp_template(phone, phone_number_id, template_id, template_name, access_token, header_image_url=None):
    """Envia um template do WhatsApp com suporte a headers de imagem."""
    url = f"https://graph.facebook.com/v20.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "pt_BR"},
            "components": []
        }
    }

    # Adiciona header de imagem, se fornecido
    if header_image_url:
        payload["template"]["components"].append({
            "type": "header",
            "parameters": [
                {
                    "type": "image",
                    "image": {
                        "link": header_image_url
                    }
                }
            ]
        })

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        log_event("Template enviado com sucesso", {"phone": phone, "template_id": template_id})
        return True
    except requests.RequestException as e:
        log_event("Erro ao enviar template", {"error": str(e), "phone": phone, "template_id": template_id})
        return False

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

        # Buscar dados da integração
        integration_id = campaign.get("integration_id")
        integration_data = get_integration_data(integration_id)
        if not integration_data:
            return {"next_message": "Erro ao carregar dados da integração."}

        access_token = integration_data.get("access_token")
        phone_number_id = campaign.get("phone_number_id", integration_data.get("phone_id"))

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
            template_id = campaign.get("template_id")
            template_name = integration_data.get("templates", {}).get(template_id, None)

            # Mapa de templates com headers de imagem
            template_image_map = {
                "1237512181095420": "https://scontent.whatsapp.net/v/t61.29466-34/491882630_1237512184428753_7748188122744518520_n.jpg?ccb=1-7&_nc_sid=8b1bef&_nc_ohc=czs90SCGkP8Q7kNvwGAfnNe&_nc_oc=Adlbe_KDuaLgblbdg87Wr-koYZpr8N4JERrY-72Le-AGOP8bLb7gLPmz8N4fq0Nb3x2Vu07ulrh3J2Z7FPOSSmpY&_nc_zt=3&_nc_ht=scontent.whatsapp.net&edm=AH51TzQEAAAA&_nc_gid=V28yBqjyNpnV29XEBVx4hQ&oh=01_Q5Aa1gH2Al5nt3y64UAGi5h6_b85aGDIvIu1hU6O29oiZvD0_w&oe=686E3A63"
            }
            header_image_url = template_image_map.get(template_id)

            if template_name:
                if not send_whatsapp_template(phone, phone_number_id, template_id, template_name, access_token, header_image_url):
                    return {"next_message": "Erro ao enviar o template inicial. Tente novamente."}

            next_question = questions[0]
            save_user_state(phone, campaign_id, next_question["id"], answers)
            log_event("Início de pesquisa", {"next_question": next_question["text"], "question_id": next_question["id"]})
            message_text = next_question["text"]
            if next_question["type"] in ["quick_reply", "multiple_choice"]:
                options = next_question.get("options", [])
                if options:
                    letters = [chr(97 + i) for i in range(len(options))]
                    message_text += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
            return {"next_message": message_text}

        current_question = next((q for q in questions if str(q["id"]) == str(current_step)), None)
        if not current_question and answers:
            ids_respondidas = sorted([int(k) for k in answers.keys() if k.isdigit()])
            if ids_respondidas:
                ultima_id = ids_respondidas[-1]
                current_question = next((q for q in questions if int(q["id"]) == ultima_id), None)

        if not current_question:
            log_event("Erro: pergunta atual não encontrada", {"current_step": current_step})
            return {"next_message": "Erro interno: pergunta atual não encontrada."}

        valid_answer = False
        confirmation_text = ""
        selected = ""
        options = current_question.get("options", [])

        log_event("Processando resposta", {"message": message, "question": current_question["text"], "question_id": current_question["id"]})

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
                confirmation_text = f"✔ Você escolheu: {selected}"

        elif current_question["type"] in ["text", "open_text"]:
            if message.strip():
                if current_question.get("requires_cpf") and not validate_cpf(message.strip()):
                    log_event("CPF inválido", {"phone": phone, "cpf": message.strip()})
                    return {"next_message": "CPF inválido. Por favor, digite um CPF válido (ex.: 123.456.789-01)."}
                answers[str(current_question["id"])] = message.strip()
                valid_answer = True
                confirmation_text = f"✔ Resposta registrada: {message.strip()}"

        if not valid_answer:
            log_event("Resposta inválida", {
                "question_id": current_question["id"],
                "question_type": current_question["type"],
                "answer": message
            })
            message_text = f"❌ Resposta inválida.\n{current_question['text']}"
            if options:
                letters = [chr(97 + i) for i in range(len(options))]
                message_text += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
            return {"next_message": message_text}

        if isinstance(current_question["id"], (int, float)) and int(current_question["id"]) > len(questions):
            log_event("Erro: id excede perguntas", {
                "question_id": current_question["id"],
                "total_questions": len(questions)
            })
            return {"next_message": "Erro interno: ID da pergunta inválido."}

        save_user_state(phone, campaign_id, current_question["id"], answers)
        log_event("Resposta salva", {"phone": phone, "campaign_id": campaign_id, "answer": selected or message.strip()})

        next_question = None
        current_index = next((i for i, q in enumerate(questions) if str(q["id"]) == str(current_question["id"])), -1)

        if valid_answer and current_index != -1:
            for q in questions[current_index + 1:]:
                if q.get("condition") and str(q["condition"]).lower() == (selected.lower() if selected else message.strip().lower()):
                    next_question = q
                    break

        if not next_question and valid_answer and current_index != -1:
            conditions = current_question.get("conditions", {})
            if selected and selected in conditions:
                action = conditions[selected]
                if isinstance(action, dict) and "jump" in action:
                    target_id = str(action["jump"])
                    next_question = next((q for q in questions if str(q["id"]) == target_id), None)
                elif action == "end":
                    next_question = None

        if not next_question and current_index != -1:
            for i in range(current_index + 1, len(questions)):
                if not questions[i].get("condition"):
                    next_question = questions[i]
                    break

        log_event("Próximo passo", {
            "de": current_question["id"],
            "para": next_question["id"] if next_question else "fim"
        })

        if next_question:
            save_user_state(phone, campaign_id, next_question["id"], answers)
            log_event("Atualizado question_id", {"new_id": next_question["id"]})
            message_text = f"{confirmation_text}\n\n{next_question['text']}"
            if next_question["type"] in ["quick_reply", "multiple_choice"]:
                options = next_question.get("options", [])
                if options:
                    letters = [chr(97 + i) for i in range(len(options))]
                    message_text += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
            return {"next_message": message_text}
        else:
            save_user_state(phone, campaign_id, None, answers)
            final_message = flow.get("outro", "Obrigado por participar!")
            if current_question.get("type") == "text" and current_question.get("message"):
                final_message = current_question["message"]
            log_event("Pesquisa finalizada", {"phone": phone, "answers": answers})
            return {"next_message": f"{confirmation_text}\n\n{final_message}"}

    except Exception as e:
        log_event("Erro no processamento", {"error": str(e), "phone": phone, "campaign_id": campaign_id})
        return {"next_message": "Ocorreu um erro ao processar sua mensagem. Tente novamente."}