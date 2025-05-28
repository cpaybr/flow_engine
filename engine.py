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

        # Use apenas um dos fluxos
        flow = None
        if campaign.get("flow_json") and campaign.get("flow_json").get("questions"):
            flow = campaign["flow_json"]
        elif campaign.get("questions_json") and campaign.get("questions_json").get("questions"):
            flow = campaign["questions_json"]
        else:
            return {"next_message": "Campanha sem perguntas definidas."}

        questions = flow.get("questions", [])
        if not questions:
            return {"next_message": "Campanha sem perguntas válidas."}

        user_state = get_user_state(phone, campaign_id)
        current_step = user_state.get("current_step")
        answers = user_state.get("answers", {})

        if not current_step or message.lower() in ["participar", "começar"]:
            next_question = questions[0]
            save_user_state(phone, campaign_id, next_question["id"], answers)
            return {"next_message": next_question["text"]}

        current_question = next((q for q in questions if str(q["id"]) == str(current_step)), None)
        if not current_question:
            return {"next_message": "Erro interno: pergunta atual não encontrada."}

        valid_answer = False
        confirmation_text = ""
        selected = ""
        options = current_question.get("options", [])

        if current_question["type"] in ["quick_reply", "multiple_choice"]:
            letters = [chr(97 + i) for i in range(len(options))]  # a, b, c...
            numbers = [str(i + 1) for i in range(len(options))]

            if message.startswith("opt_"):
                try:
                    idx = int(message.split("_")[1])
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        valid_answer = True
                except (ValueError, IndexError):
                    pass

            elif message.lower() in letters:
                try:
                    idx = letters.index(message.lower())
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        valid_answer = True
                except Exception:
                    pass

            elif message in numbers:
                try:
                    idx = int(message) - 1
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        valid_answer = True
                except Exception:
                    pass

            if valid_answer:
                answers[str(current_question["id"])] = selected
                confirmation_text = f"✔️ Você escolheu: {selected}"

        elif current_question["type"] in ["text", "open_text"]:
            if message.strip():
                answers[str(current_question["id"])] = message.strip()
                valid_answer = True

        if not valid_answer:
            log_event("Resposta inválida", {
                "question_id": current_question["id"],
                "question_type": current_question["type"],
                "answer": message
            })

            if options:
                letras = [chr(97 + i) for i in range(len(options))]
                op_texto = "\n".join([f"{letras[i]}) {opt}" for i, opt in enumerate(options)])
                return {"next_message": f"❌ Resposta inválida. Escolha uma das opções abaixo:\n{op_texto}"}
            else:
                return {"next_message": current_question["text"]}

        # Definir próxima pergunta
        next_question = None

        for q in questions:
            if q.get("condition") and str(q["condition"]).lower() == answers[str(current_question["id"])].lower():
                next_question = q
                break

        if not next_question:
            current_index = next((i for i, q in enumerate(questions) if str(q["id"]) == str(current_question["id"])), -1)
            if current_index != -1 and current_index + 1 < len(questions):
                next_question = questions[current_index + 1]

        # Log antes de salvar estado
        log_event("Salvando novo passo", {
            "phone": phone,
            "campanha": campaign_id,
            "de": current_step,
            "para": next_question["id"] if next_question else "fim"
        })

        if next_question:
            save_user_state(phone, campaign_id, next_question["id"], answers)
            return {"next_message": f"{confirmation_text}\n\n{next_question['text']}"}
        else:
            save_user_state(phone, campaign_id, None, answers)
            outro = flow.get("outro", "Obrigado por participar da pesquisa!")
            return {"next_message": f"{confirmation_text}\n\n{outro}"}

    except Exception as e:
        log_event("Erro no processamento", {"error": str(e)})
        return {"next_message": "Ocorreu um erro ao processar sua mensagem."}
