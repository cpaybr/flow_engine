from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code
import logging
import json
import re
import html
import unicodedata
import traceback
from typing import Dict, Any, Union

def normalize_text(text: str) -> str:
    """Normaliza caracteres especiais e entidades HTML"""
    if not text:
        return text
    text = html.unescape(text)
    text = unicodedata.normalize('NFKD', text)
    return text.encode('utf-8', 'ignore').decode('utf-8')

def is_valid_cpf(cpf: str) -> bool:
    """Valida CPF com tratamento de caracteres"""
    try:
        cpf = re.sub(r'[^0-9]', '', normalize_text(cpf))
        if len(cpf) != 11 or cpf == cpf[0] * 11:
            return False
            
        soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
        d1 = (soma * 10) % 11
        d1 = d1 if d1 < 10 else 0
        
        soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
        d2 = (soma * 10) % 11
        d2 = d2 if d2 < 10 else 0
        
        return cpf[-2:] == f"{d1}{d2}"
    except Exception:
        return False

logging.basicConfig(
    filename='/home/flow_engine/engine.log',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True,
    encoding='utf-8'
)

petition_logger = logging.getLogger('petition')
petition_handler = logging.FileHandler(
    '/home/flow_engine/petition.log',
    encoding='utf-8'
)
petition_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
petition_logger.addHandler(petition_handler)
petition_logger.setLevel(logging.INFO)

def log_event(message: str, data: Dict = {}):
    """Log seguro com tratamento de caracteres"""
    safe_data = {
        k: normalize_text(v) if isinstance(v, str) else v 
        for k, v in data.items()
    }
    logging.info(json.dumps(
        {"message": normalize_text(message), "data": safe_data},
        ensure_ascii=False
    ))

def log_petition_event(message: str, data: Dict = {}):
    """Log específico para petições"""
    safe_data = {
        k: normalize_text(v) if isinstance(v, str) else v 
        for k, v in data.items()
    }
    petition_logger.info(json.dumps(
        {"message": normalize_text(message), "data": safe_data},
        ensure_ascii=False
    ))

async def process_message(phone: str, campaign_id: str, message: str) -> Dict[str, Any]:
    try:
        # Normalização inicial
        phone = normalize_text(phone)
        campaign_id = normalize_text(campaign_id)
        message = normalize_text(message.strip())

        log_event("Iniciando processamento", {
            "phone": phone,
            "campaign_id": campaign_id,
            "message": message
        })

        if message.lower().startswith("começar "):
            code = normalize_text(message.split(" ")[1]).upper()
            campaign = get_campaign_by_code(code)
            if not campaign:
                return {"next_message": "Código de campanha inválido."}
            campaign_id = normalize_text(campaign['campaign_id'])
            save_user_state(phone, campaign_id, None, {})
            message = "começar"

        campaign = get_campaign(campaign_id)
        if not campaign:
            return {"next_message": "Erro ao carregar campanha."}

        def safe_json_load(data):
            if isinstance(data, str):
                try:
                    return json.loads(normalize_text(data))
                except json.JSONDecodeError:
                    return {}
            return data or {}

        flow_json = safe_json_load(campaign.get("flow_json"))
        questions_json = safe_json_load(campaign.get("questions_json"))
        flow = questions_json if questions_json.get("questions") else flow_json

        questions = []
        for q in flow.get("questions", []):
            safe_q = {
                **q,
                "id": normalize_text(str(q.get("id"))),
                "text": normalize_text(q.get("text", "")),
                "type": normalize_text(q.get("type", "")),
                "options": [normalize_text(opt) for opt in q.get("options", [])],
                "condition": normalize_text(q["condition"]) if "condition" in q else None
            }
            questions.append(safe_q)

        if not questions:
            return {"next_message": "Campanha sem perguntas válidas."}

        user_state = get_user_state(phone, campaign_id)
        current_step = user_state.get("current_step")
        answers = user_state.get("answers", {})

        log_event("Estado do usuário carregado", {
            "current_step": current_step,
            "answers": {k: normalize_text(v) if isinstance(v, str) else v for k, v in answers.items()}
        })

        if not current_step or message.lower() in ["participar", "começar", "assinar"]:
            next_question = questions[0]
            save_user_state(phone, campaign_id, next_question["id"], answers)
            message_text = next_question["text"]
            
            if next_question["type"] in ["quick_reply", "multiple_choice"]:
                options = next_question.get("options", [])
                if options:
                    letters = [chr(97 + i) for i in range(len(options))]
                    message_text += "\n" + "\n".join([
                        f"{letters[i]}) {opt}" for i, opt in enumerate(options)
                    ])
            return {"next_message": message_text}

        current_question = next(
            (q for q in questions if str(q["id"]) == str(current_step)),
            None
        )

        if not current_question and answers:
            ids_respondidas = sorted([int(k) for k in answers.keys()])
            ultima_id = ids_respondidas[-1]
            current_question = next(
                (q for q in questions if int(q["id"]) == ultima_id),
                None
            )

        if not current_question:
            return {"next_message": "Erro interno: pergunta atual não encontrada."}

        valid_answer = False
        confirmation_text = ""
        selected = ""
        options = current_question.get("options", [])

        log_event("Processando resposta", {
            "message": message,
            "question": current_question["id"]
        })

        if current_question["type"] in ["quick_reply", "multiple_choice"]:
            letters = [chr(97 + i) for i in range(len(options))]
            numbers = [str(i + 1) for i in range(len(options))]
            option_map = {
                opt.lower(): f"opt_{i}" 
                for i, opt in enumerate(options)
            }

            if message.startswith("opt_"):
                try:
                    idx = int(message.split("_")[1])
                    if 0 <= idx < len(options):
                        selected = normalize_text(options[idx])
                        valid_answer = True
                except (ValueError, IndexError):
                    pass
            elif message.lower() in letters:
                try:
                    idx = letters.index(message.lower())
                    if 0 <= idx < len(options):
                        selected = normalize_text(options[idx])
                        valid_answer = True
                except ValueError:
                    pass
            elif message in numbers:
                try:
                    idx = int(message) - 1
                    if 0 <= idx < len(options):
                        selected = normalize_text(options[idx])
                        valid_answer = True
                except ValueError:
                    pass
            elif message.lower() in option_map:
                message = option_map[message.lower()]
                idx = int(message.split("_")[1])
                selected = normalize_text(options[idx])
                valid_answer = True

            if valid_answer:
                answers[str(current_question["id"])] = selected
                confirmation_text = f"✔️ Você escolheu: {selected}"

        elif current_question["type"] in ["text", "open_text"]:
            question_text = current_question.get("text", "").lower()
            response = message.strip()
            if response:
                if "cpf" in question_text:
                    if not is_valid_cpf(response):
                        log_event("CPF inválido detectado", {
                            "cpf": response,
                            "phone": phone
                        })
                        return {
                            "next_message": "❌ CPF inválido. Por favor, digite um CPF válido com 11 dígitos (apenas números)."
                        }
                    cpf_limpo = re.sub(r'[^0-9]', '', response)
                    answers[str(current_question["id"])] = cpf_limpo
                    valid_answer = True
                    confirmation_text = f"✔️ CPF registrado: {cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-{cpf_limpo[9:]}"
                else:
                    answers[str(current_question["id"])] = normalize_text(response)
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
                message_text += "\n" + "\n".join([
                    f"{letters[i]}) {opt}" for i, opt in enumerate(options)
                ])
            return {"next_message": message_text}

        if int(current_question["id"]) > len(questions):
            log_event("Erro: question_id excede número de perguntas", {
                "question_id": current_question["id"],
                "total_questions": len(questions)
            })
            return {"next_message": "Erro interno: ID da pergunta inválido."}

        save_user_state(phone, campaign_id, current_question["id"], answers)
        log_event("Resposta salva", {
            "phone": phone,
            "campaign_id": campaign_id,
            "question_id": current_question["id"],
            "answer": selected or message.strip()
        })

        next_question = None
        current_index = next(
            (i for i, q in enumerate(questions) 
            if str(q["id"]) == str(current_question["id"])),
            -1
        )

        if valid_answer and current_index != -1:
            for q in questions[current_index + 1:]:
                if q.get("condition") and str(q["condition"]).lower() == (
                    selected.lower() if selected else message.strip().lower()
                ):
                    next_question = q
                    break

        if not next_question and current_index != -1:
            for i in range(current_index + 1, len(questions)):
                if not questions[i].get("condition"):
                    next_question = questions[i]
                    break

        log_event("Determinado próximo passo", {
            "de": current_question["id"],
            "para": next_question["id"] if next_question else "fim"
        })

        if next_question:
            save_user_state(phone, campaign_id, next_question["id"], answers)
            message_text = f"{normalize_text(confirmation_text)}\n\n{normalize_text(next_question['text'])}"
            if next_question["type"] in ["quick_reply", "multiple_choice"]:
                options = next_question.get("options", [])
                if options:
                    letters = [chr(97 + i) for i in range(len(options))]
                    message_text += "\n" + "\n".join([
                        f"{letters[i]}) {normalize_text(opt)}" for i, opt in enumerate(options)
                    ])
            return {"next_message": message_text}
        else:
            save_user_state(phone, campaign_id, None, answers)
            final_message = normalize_text(
                flow.get("outro", "Obrigado por participar da pesquisa!")
            )
            if (current_question.get("type") == "text" and 
                current_question.get("message")):
                final_message = normalize_text(current_question["message"])
            return {"next_message": final_message}

    except Exception as e:
        log_event("Erro no processamento", {
            "error": normalize_text(str(e)),
            "traceback": normalize_text(traceback.format_exc()),
            "phone": phone,
            "message": message,
            "campaign_id": campaign_id
        })
        return {
            "next_message": "⚠️ Ocorreu um erro interno. Por favor, tente novamente."
        }

if __name__ == "__main__":
    import asyncio
    # Teste seguro
    test_result = asyncio.run(process_message(
        "+5511999999999",
        "test-campaign",
        "Sim/Não/Çãõ"
    ))
    print(test_result)