from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code
import logging
import json
import re

logging.basicConfig(
    filename='engine.log',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)

def log_event(message, data={}):
    log_entry = {'message': message, 'data': data}
    logging.info(json.dumps(log_entry))

def validate_cpf(cpf: str) -> bool:
    cpf = re.sub(r'[^0-9]', '', cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    
    # Validate first digit
    sum = 0
    for i in range(9):
        sum += int(cpf[i]) * (10 - i)
    remainder = (sum * 10) % 11
    if remainder == 10:
        remainder = 0
    if remainder != int(cpf[9]):
        return False
    
    # Validate second digit
    sum = 0
    for i in range(10):
        sum += int(cpf[i]) * (11 - i)
    remainder = (sum * 10) % 11
    if remainder == 10:
        remainder = 0
    if remainder != int(cpf[10]):
        return False
    
    return True

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

        flow = campaign.get("questions_json") or campaign.get("flow_json", {})
        if not flow.get("questions"):
            return {"next_message": "Campanha sem perguntas definidas."}

        questions = flow["questions"]
        user_state = get_user_state(phone, campaign_id)
        current_step = user_state.get("current_step")
        answers = user_state.get("answers", {})

        # Handle start of survey
        if not current_step or message.lower() in ["participar", "começar", "assinar"]:
            return handle_survey_start(phone, campaign_id, questions, flow)

        current_question = next((q for q in questions if str(q["id"]) == str(current_step)), None)
        if not current_question:
            return {"next_message": "Erro interno: pergunta atual não encontrada."}

        # Handle special question types
        if current_question.get("requires_cpf"):
            if not validate_cpf(message):
                return {"next_message": "CPF inválido. Digite apenas os 11 números."}
            answers[str(current_question["id"])] = message
            save_user_state(phone, campaign_id, current_question["id"], answers)
            return get_next_question(phone, campaign_id, questions, current_question, answers, flow)

        # Process answer
        answer_processed = process_answer(current_question, message)
        if not answer_processed["valid"]:
            return {"next_message": answer_processed["error_message"]}

        answers[str(current_question["id"])] = answer_processed["answer"]
        save_user_state(phone, campaign_id, current_question["id"], answers)

        # Handle conditional flows
        if answer_processed["condition_met"]:
            conditional_question = find_conditional_question(questions, current_question, answer_processed["answer"])
            if conditional_question:
                save_user_state(phone, campaign_id, conditional_question["id"], answers)
                return format_question_message(conditional_question, answer_processed["confirmation"])

        # Get next question
        return get_next_question(phone, campaign_id, questions, current_question, answers, flow, answer_processed["confirmation"])

    except Exception as e:
        log_event("ERRO CRÍTICO", {"error": str(e)})
        return {"next_message": "Ocorreu um erro ao processar sua mensagem."}

def handle_survey_start(phone, campaign_id, questions, flow):
    first_question = questions[0]
    save_user_state(phone, campaign_id, first_question["id"], {})
    return format_question_message(first_question)

def process_answer(question, message):
    result = {
        "valid": False,
        "answer": "",
        "confirmation": "",
        "condition_met": False,
        "error_message": ""
    }

    if question["type"] in ["quick_reply", "multiple_choice"]:
        options = question.get("options", [])
        option_map = {opt.lower(): opt for opt in options}
        
        # Handle button replies (opt_X)
        if message.startswith("opt_"):
            try:
                idx = int(message.split("_")[1])
                if 0 <= idx < len(options):
                    result["answer"] = options[idx]
                    result["valid"] = True
                    result["confirmation"] = f"✔️ Você escolheu: {options[idx]}"
            except:
                pass
        
        # Handle text replies
        elif message.lower() in option_map:
            result["answer"] = option_map[message.lower()]
            result["valid"] = True
            result["confirmation"] = f"✔️ Você escolheu: {option_map[message.lower()]}"
            result["condition_met"] = True
        
        if not result["valid"]:
            result["error_message"] = format_error_message(question)

    elif question["type"] in ["text", "open_text"]:
        if message.strip():
            result["answer"] = message.strip()
            result["valid"] = True
            result["confirmation"] = f"✔️ Resposta registrada: {message.strip()}"
        else:
            result["error_message"] = "Por favor, digite sua resposta."

    return result

def format_error_message(question):
    message = f"❌ Resposta inválida. Por favor, responda com uma das opções:\n{question['text']}"
    if question.get("options"):
        letters = [chr(97 + i) for i in range(len(question["options"]))]
        message += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(question["options"])])
    return message

def find_conditional_question(questions, current_question, answer):
    current_index = next((i for i, q in enumerate(questions) if q["id"] == current_question["id"]), -1)
    if current_index == -1:
        return None
    
    for q in questions[current_index + 1:]:
        if q.get("condition") and str(q["condition"]).lower() == answer.lower():
            return q
        if q.get("target") and isinstance(q.get("target"), int):
            target_question = next((quest for quest in questions if quest["id"] == q["target"]), None)
            if target_question and str(q["condition"]).lower() == answer.lower():
                return target_question
    
    return None

def get_next_question(phone, campaign_id, questions, current_question, answers, flow, confirmation=""):
    current_index = next((i for i, q in enumerate(questions)) if q["id"] == current_question["id"] else -1
    if current_index == -1:
        return {"next_message": "Erro interno: índice da pergunta não encontrado."}

    # Find next non-conditional question
    next_question = None
    for q in questions[current_index + 1:]:
        if not q.get("condition"):
            next_question = q
            break

    if next_question:
        save_user_state(phone, campaign_id, next_question["id"], answers)
        message = f"{confirmation}\n\n{next_question['text']}" if confirmation else next_question['text']
        
        if next_question["type"] in ["quick_reply", "multiple_choice"]:
            options = next_question.get("options", [])
            if options:
                letters = [chr(97 + i) for i in range(len(options))]
                message += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
        
        return {"next_message": message}
    else:
        save_user_state(phone, campaign_id, None, answers)
        final_message = flow.get("outro", "Obrigado por participar da pesquisa!")
        return {"next_message": f"{confirmation}\n\n{final_message}"}

def format_question_message(question, prefix=""):
    message = f"{prefix}\n\n{question['text']}" if prefix else question['text']
    
    if question["type"] in ["quick_reply", "multiple_choice"]:
        options = question.get("options", [])
        if options:
            letters = [chr(97 + i) for i in range(len(options))]
            message += "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])
    
    return {"next_message": message}