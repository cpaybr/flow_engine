import asyncio
import json
import re
import html
import unicodedata
import traceback
import logging
from typing import Dict, Any, Union, Optional
from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code

# Contador simulado em memória (temporário)
petition_counts = {}

# Logging configuration
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

def normalize_text(text: Any) -> str:
    """Normalizes special characters and HTML entities, handling non-string inputs."""
    if text is None:
        return ""
    if not isinstance(text, str):
        logging.warning(f"Non-string input received in normalize_text: {type(text)} - {text}")
        return str(text) if text else ""
    text = html.unescape(text)
    text = unicodedata.normalize('NFKD', text)
    return text.encode('utf-8', 'ignore').decode('utf-8')

def is_valid_cpf(cpf: str) -> bool:
    """Validates a CPF with character cleaning."""
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

def log_event(message: str, data: Dict = {}, survey_type: str = "unknown"):
    """Logs an event with safe character handling."""
    safe_data = {
        k: normalize_text(v) if isinstance(v, str) else v
        for k, v in data.items()
    }
    safe_data['survey_type'] = survey_type
    logging.info(json.dumps(
        {"message": normalize_text(message), "data": safe_data},
        ensure_ascii=False
    ))

def log_petition_event(message: str, data: Dict = {}):
    """Logs petition-specific events."""
    safe_data = {
        k: normalize_text(v) if isinstance(v, str) else v
        for k, v in data.items()
    }
    petition_logger.info(json.dumps(
        {"message": normalize_text(message), "data": safe_data},
        ensure_ascii=False
    ))

class SurveyProcessor:
    """Handles processing of different survey types."""
    def __init__(self, campaign: Dict, phone: str, campaign_id: str):
        self.campaign = campaign
        self.phone = normalize_text(phone)
        self.campaign_id = normalize_text(campaign_id)
        self.survey_type = self._determine_survey_type()
        self.questions = self._load_questions()
        self.user_state = get_user_state(self.phone, self.campaign_id)

    def _determine_survey_type(self) -> str:
        """Determines the survey type based on campaign data."""
        questions_json = self._safe_json_load(self.campaign.get("questions_json", {}))
        flow_json = self._safe_json_load(self.campaign.get("flow_json", {}))
        survey_type = questions_json.get("type", flow_json.get("type", "standard")).lower()
        log_event("Survey type determined", {"type": survey_type}, survey_type)
        return survey_type

    def _safe_json_load(self, data: Any) -> Dict:
        """Safely loads JSON data, handling strings and invalid JSON."""
        if isinstance(data, str):
            try:
                return json.loads(normalize_text(data))
            except json.JSONDecodeError:
                return {}
        return data or {}

    def _load_questions(self) -> list:
        """Loads and normalizes questions from campaign data."""
        questions_json = self._safe_json_load(self.campaign.get("questions_json", {}))
        flow_json = self._safe_json_load(self.campaign.get("flow_json", {}))
        flow = questions_json if questions_json.get("questions") else flow_json
        questions = []
        for q in flow.get("questions", []):
            safe_q = {
                "id": normalize_text(str(q.get("id"))),
                "text": normalize_text(q.get("text", "")),
                "type": normalize_text(q.get("type", "text")),
                "options": [
                    {
                        "text": opt.get("text", str(opt)) if isinstance(opt, dict) else normalize_text(opt),
                        "action": opt.get("action") if isinstance(opt, dict) else None,
                        "target": opt.get("target") if isinstance(opt, dict) else None
                    } if isinstance(opt, dict) else {"text": normalize_text(opt)}
                    for opt in q.get("options", [])
                ],
                "condition": normalize_text(q["condition"]) if "condition" in q else None,
                "message": normalize_text(q.get("message", "")) if "message" in q else None
            }
            questions.append(safe_q)
        log_event("Questions loaded", {"question_count": len(questions)}, self.survey_type)
        return questions

    def _validate_campaign(self) -> bool:
        """Validates the campaign structure."""
        if not self.campaign:
            log_event("Invalid campaign: No campaign data", {}, self.survey_type)
            return False
        if not self.questions:
            log_event("Invalid campaign: No questions found", {}, self.survey_type)
            return False
        return True

    def _format_options(self, question: Dict) -> Dict[str, Any]:
        """Formats question options for display, returning an interactive payload."""
        options = question.get("options", [])
        if not options:
            return {"text": ""}
        if question["type"] == "quick_reply" and len(options) <= 3:
            buttons = [
                {
                    "type": "reply",
                    "reply": {
                        "id": f"opt_{i}",
                        "title": opt["text"][:20]  # WhatsApp limits button titles to 20 characters
                    }
                } for i, opt in enumerate(options)
            ]
            return {
                "interactive": {
                    "type": "button",
                    "body": {"text": question["text"]},
                    "action": {"buttons": buttons}
                }
            }
        else:
            sections = [{
                "rows": [
                    {
                        "id": f"opt_{i}",
                        "title": opt["text"][:24],  # WhatsApp limits list item titles to 24 characters
                        "description": ""
                    } for i, opt in enumerate(options)
                ]
            }]
            return {
                "interactive": {
                    "type": "list",
                    "body": {"text": question["text"]},
                    "action": {
                        "button": "Escolha uma opção",
                        "sections": sections
                    }
                }
            }

    def _validate_answer(self, question: Dict, message: str) -> tuple[bool, str, str]:
        """Validates the user's answer and returns (is_valid, selected_answer, confirmation_text)."""
        options = question.get("options", [])
        question_type = question["type"]
        message = normalize_text(message.strip())
        log_event("Validating answer", {
            "question_id": question["id"],
            "question_type": question_type,
            "message": message,
            "options": [opt["text"] for opt in options]
        }, self.survey_type)

        if not message:
            log_event("Empty message received", {"question_id": question["id"]}, self.survey_type)
            return False, "", "❌ Resposta inválida. Por favor, selecione uma opção."

        if question_type in ["quick_reply", "multiple_choice"]:
            letters = [chr(97 + i) for i in range(len(options))]
            numbers = [str(i + 1) for i in range(len(options))]
            option_map = {opt["text"].lower(): f"opt_{i}" for i, opt in enumerate(options)}

            if message.startswith("opt_"):
                try:
                    idx = int(message.split("_")[1])
                    if 0 <= idx < len(options):
                        log_event("Valid answer found", {"index": idx, "answer": options[idx]["text"]}, self.survey_type)
                        return True, options[idx]["text"], f"✔️ Você escolheu: {options[idx]['text']}"
                    else:
                        log_event("Invalid opt index", {"index": idx, "options_length": len(options)}, self.survey_type)
                except (ValueError, IndexError) as e:
                    log_event("Error parsing opt_", {"error": str(e), "message": message}, self.survey_type)
            elif message.lower() in letters:
                try:
                    idx = letters.index(message.lower())
                    log_event("Valid answer found by letter", {"letter": message.lower(), "index": idx, "answer": options[idx]["text"]}, self.survey_type)
                    return True, options[idx]["text"], f"✔️ Você escolheu: {options[idx]['text']}"
                except ValueError as e:
                    log_event("Invalid letter", {"error": str(e), "message": message}, self.survey_type)
            elif message in numbers:
                try:
                    idx = int(message) - 1
                    if 0 <= idx < len(options):
                        log_event("Valid answer found by number", {"number": message, "index": idx, "answer": options[idx]["text"]}, self.survey_type)
                        return True, options[idx]["text"], f"✔️ Você escolheu: {options[idx]['text']}"
                    else:
                        log_event("Invalid number index", {"index": idx, "options_length": len(options)}, self.survey_type)
                except ValueError as e:
                    log_event("Error parsing number", {"error": str(e), "message": message}, self.survey_type)
            elif message.lower() in option_map:
                idx = int(option_map[message.lower()].split("_")[1])
                log_event("Valid answer found by text", {"text": message.lower(), "index": idx, "answer": options[idx]["text"]}, self.survey_type)
                return True, options[idx]["text"], f"✔️ Você escolheu: {options[idx]['text']}"
            else:
                log_event("No matching answer", {"message": message, "options_map": option_map}, self.survey_type)
        elif question_type in ["text", "open_text"]:
            if "cpf" in question["text"].lower() and self.survey_type == "petition":
                if not is_valid_cpf(message):
                    log_event("Invalid CPF", {"cpf": message}, self.survey_type)
                    return False, "", "❌ CPF inválido. Por favor, digite um CPF válido com 11 dígitos (apenas números)."
                cpf_limpo = re.sub(r'[^0-9]', '', message)
                formatted_cpf = f"{cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-{cpf_limpo[9:]}"
                log_event("Valid CPF", {"cpf": formatted_cpf}, self.survey_type)
                return True, cpf_limpo, f"✔️ CPF registrado: {formatted_cpf}"
            if message:
                log_event("Valid open text answer", {"answer": message}, self.survey_type)
                return True, message, f"✔️ Resposta registrada: {message}"
            else:
                log_event("Empty open text answer", {}, self.survey_type)
        log_event("Answer validation failed", {"message": message, "question_type": question_type}, self.survey_type)
        return False, "", "❌ Resposta inválida. Por favor, selecione uma opção."

    def _get_next_question(self, current_question: Dict, selected_answer: str) -> Optional[Dict]:
        """Determines the next question based on the current question and answer."""
        current_index = next(
            (i for i, q in enumerate(self.questions) if str(q["id"]) == str(current_question["id"])),
            -1
        )
        if current_index == -1:
            log_event("Current question index not found", {"current_id": current_question["id"]}, self.survey_type)
            return None

        log_event("Searching for next question", {
            "current_index": current_index,
            "current_id": current_question["id"],
            "selected_answer": selected_answer
        }, self.survey_type)

        # Check if the selected answer has a specific target
        for opt in current_question.get("options", []):
            if opt["text"].lower() == normalize_text(selected_answer).lower() and opt.get("target"):
                target_id = opt["target"]
                next_question = next(
                    (q for q in self.questions if str(q["id"]) == str(target_id)),
                    None
                )
                if next_question:
                    log_event("Next question found by option target", {
                        "next_id": next_question["id"],
                        "target": target_id
                    }, self.survey_type)
                    return next_question
                log_event("Target question not found", {"target": target_id}, self.survey_type)

        # Check for conditional questions
        for i, q in enumerate(self.questions[current_index + 1:], start=current_index + 1):
            if q.get("condition") and normalize_text(q["condition"]).lower() == normalize_text(selected_answer).lower():
                log_event("Next question found by condition", {
                    "next_id": q["id"],
                    "condition": q["condition"]
                }, self.survey_type)
                return q

        # Fall back to the next non-conditional question
        for i, q in enumerate(self.questions[current_index + 1:], start=current_index + 1):
            if not q.get("condition"):
                log_event("Next non-conditional question found", {"next_id": q["id"]}, self.survey_type)
                return q

        log_event("No next question found", {}, self.survey_type)
        return None

    async def process(self, message: str) -> Dict[str, Any]:
        """Processes an incoming message and returns the next message."""
        try:
            message = normalize_text(message.strip())
            log_event("Processing message", {
                "phone": self.phone,
                "campaign_id": self.campaign_id,
                "message": message
            }, self.survey_type)

            if not self._validate_campaign():
                return {"next_message": "Campanha sem perguntas válidas."}

            current_step = self.user_state.get("current_step")
            answers = self.user_state.get("answers", {})
            log_event("Current state", {
                "current_step": current_step,
                "answers": answers
            }, self.survey_type)

            # Handle campaign start via code
            if message.lower().startswith("começar "):
                code = normalize_text(message.split(" ")[1]).upper()
                campaign = get_campaign_by_code(code)
                if not campaign:
                    return {"next_message": "Código de campanha inválido."}
                self.campaign_id = normalize_text(campaign['campaign_id'])
                self.campaign = campaign
                self.survey_type = self._determine_survey_type()
                self.questions = self._load_questions()
                if not save_user_state(self.phone, self.campaign_id, None, {}):
                    log_event("Failed to reset user state", {
                        "phone": self.phone,
                        "campaign_id": self.campaign_id
                    }, self.survey_type)
                    return {"next_message": "⚠️ Erro ao iniciar a pesquisa. Tente novamente."}
                message = "começar"

            # Handle survey initiation
            if not current_step or message.lower() in ["participar", "começar", "assinar"]:
                next_question = self.questions[0]
                answers = {}  # Reset answers on start
                if not save_user_state(self.phone, self.campaign_id, next_question["id"], answers):
                    log_event("Failed to save initial state", {
                        "phone": self.phone,
                        "campaign_id": self.campaign_id,
                        "step": next_question["id"]
                    }, self.survey_type)
                    return {"next_message": "⚠️ Erro ao iniciar a pesquisa. Tente novamente."}
                log_event("Survey started", {
                    "phone": self.phone,
                    "campaign_id": self.campaign_id,
                    "first_question_id": next_question["id"]
                }, self.survey_type)
                if next_question["type"] in ["quick_reply", "multiple_choice"]:
                    return self._format_options(next_question)
                return {"next_message": next_question["text"]}

            # Find current question
            current_question = next(
                (q for q in self.questions if str(q["id"]) == str(current_step)),
                None
            )
            if not current_question:
                log_event("Current question not found, checking answers", {
                    "current_step": current_step,
                    "answers": answers
                }, self.survey_type)
                if answers:
                    ids_respondidas = sorted([k for k in answers.keys() if k.isalnum()])
                    ultima_id = ids_respondidas[-1] if ids_respondidas else None
                    current_question = next(
                        (q for q in self.questions if str(q["id"]) == ultima_id),
                        None
                    )
            if not current_question:
                log_event("Current question not found", {"current_step": current_step}, self.survey_type)
                return {"next_message": "Erro interno: pergunta atual não encontrada."}

            # Validate answer
            valid_answer, selected_answer, confirmation_text = self._validate_answer(current_question, message)
            if not valid_answer:
                message_text = f"❌ Resposta inválida. Escolha uma das opções abaixo:\n{current_question['text']}"
                if current_question["type"] in ["quick_reply", "multiple_choice"]:
                    return self._format_options(current_question)
                return {"next_message": message_text}

            # Save answer
            answers[str(current_question["id"])] = selected_answer
            log_event("Answer recorded", {
                "question_id": current_question["id"],
                "answer": selected_answer,
                "answers": answers
            }, self.survey_type)
            if not save_user_state(self.phone, self.campaign_id, current_question["id"], answers):
                log_event("Failed to save answer", {
                    "question_id": current_question["id"],
                    "answer": selected_answer,
                    "answers": answers
                }, self.survey_type)
                return {"next_message": "⚠️ Erro ao salvar resposta. Tente novamente."}
            self.user_state = get_user_state(self.phone, self.campaign_id)  # Refresh state
            log_event("Answer saved and state refreshed", {
                "question_id": current_question["id"],
                "answer": selected_answer,
                "updated_answers": self.user_state.get("answers", {})
            }, self.survey_type)

            # Determine next question
            next_question = self._get_next_question(current_question, selected_answer)
            if next_question:
                if not save_user_state(self.phone, self.campaign_id, next_question["id"], answers):
                    log_event("Failed to save next question state", {
                        "next_question_id": next_question["id"],
                        "answers": answers
                    }, self.survey_type)
                    return {"next_message": "⚠️ Erro ao avançar para a próxima pergunta. Tente novamente."}
                self.user_state = get_user_state(self.phone, self.campaign_id)  # Refresh state
                log_event("State updated for next question", {
                    "next_question_id": next_question["id"],
                    "answers": self.user_state.get("answers", {})
                }, self.survey_type)
                if next_question["type"] in ["quick_reply", "multiple_choice"]:
                    interactive_payload = self._format_options(next_question)
                    interactive_payload["interactive"]["header"] = {
                        "type": "text",
                        "text": confirmation_text
                    }
                    return interactive_payload
                return {"next_message": f"{confirmation_text}\n\n{next_question['text']}"}

            # Survey completion
            if not answers:
                log_event("Attempted to complete survey with no answers", {}, self.survey_type)
                return {"next_message": "⚠️ Nenhuma resposta registrada. Por favor, reinicie a pesquisa."}
            if not save_user_state(self.phone, self.campaign_id, None, answers):
                log_event("Failed to save completion state", {"answers": answers}, self.survey_type)
                return {"next_message": "⚠️ Erro ao finalizar a pesquisa. Tente novamente."}
            self.user_state = get_user_state(self.phone, self.campaign_id)  # Refresh state
            final_message = self._safe_json_load(self.campaign.get("questions_json", {})).get(
                "outro", "Obrigado por participar da pesquisa!"
            )
            if self.survey_type == "petition":
                petition_counts.setdefault(self.campaign_id, 0)
                petition_counts[self.campaign_id] += 1
                count = petition_counts[self.campaign_id]
count_text = "assinatura coletada" if count == 1 else "assinaturas coletadas"
final_message = final_message.replace("[CONTADOR]", f"{count} {count_text}")
                log_petition_event("Petition completed", {
                    "phone": self.phone,
                    "campaign_id": self.campaign_id,
                    "count": petition_counts[self.campaign_id],
                    "answers": answers
                })
            log_event("Survey completed", {
                "phone": self.phone,
                "campaign_id": self.campaign_id,
                "answers": answers
            }, self.survey_type)
            return {
                "next_message": f"{confirmation_text}\n\n{final_message}",
                "completed": True,
                "answers": answers
            }

        except Exception as e:
            log_event("Processing error", {
                "error": normalize_text(str(e)),
                "traceback": normalize_text(traceback.format_exc()),
                "phone": self.phone,
                "message": message
            }, self.survey_type)
            return {"next_message": "⚠️ Ocorreu um erro interno. Por favor, tente novamente."}

async def process_message(phone: str, campaign_id: str, message: str) -> Dict[str, Any]:
    """Entrypoint for processing messages."""
    campaign = get_campaign(campaign_id)
    if not campaign:
        log_event("Campaign not found", {"campaign_id": campaign_id}, "unknown")
        return {"next_message": "Erro ao carregar campanha."}
    processor = SurveyProcessor(campaign, phone, campaign_id)
    return await processor.process(message)

if __name__ == "__main__":
    # Test the processor
    test_result = asyncio.run(process_message(
        "+5511999999999",
        "test-campaign",
        "Sim/Não/Çãõ"
    ))
    print(test_result)