import asyncio
import json
import re
import html
import unicodedata
import traceback
import logging
from typing import Dict, Any, Union, Optional
from supabase_client import get_campaign, get_user_state, save_user_state, get_campaign_by_code

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

def normalize_text(text: str) -> str:
    """Normalizes special characters and HTML entities."""
    if not text:
        return text
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
                "options": [normalize_text(opt) for opt in q.get("options", [])],
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

    def _format_options(self, question: Dict) -> str:
        """Formats question options for display (fallback for non-interactive cases)."""
        options = question.get("options", [])
        if not options:
            return ""
        letters = [chr(97 + i) for i in range(len(options))]
        return "\n" + "\n".join([f"{letters[i]}) {opt}" for i, opt in enumerate(options)])

    def _validate_answer(self, question: Dict, message: str) -> tuple[bool, str, str]:
        """Validates the user's answer and returns (is_valid, selected_answer, confirmation_text)."""
        options = question.get("options", [])
        question_type = question["type"]
        message = normalize_text(message.strip())

        if question_type in ["quick_reply", "multiple_choice"]:
            letters = [chr(97 + i) for i in range(len(options))]
            numbers = [str(i + 1) for i in range(len(options))]
            option_map = {opt.lower(): f"opt_{i}" for i, opt in enumerate(options)}

            if message.startswith("opt_"):
                try:
                    idx = int(message.split("_")[1])
                    if 0 <= idx < len(options):
                        return True, options[idx], f"✔️ Você escolheu: {options[idx]}"
                except (ValueError, IndexError):
                    pass
            elif message.lower() in letters:
                try:
                    idx = letters.index(message.lower())
                    return True, options[idx], f"✔️ Você escolheu: {options[idx]}"
                except ValueError:
                    pass
            elif message in numbers:
                try:
                    idx = int(message) - 1
                    if 0 <= idx < len(options):
                        return True, options[idx], f"✔️ Você escolheu: {options[idx]}"
                except ValueError:
                    pass
            elif message.lower() in option_map:
                idx = int(option_map[message.lower()].split("_")[1])
                return True, options[idx], f"✔️ Você escolheu: {options[idx]}"
        elif question_type in ["text", "open_text"]:
            if "cpf" in question["text"].lower() and self.survey_type == "petition":
                if not is_valid_cpf(message):
                    log_event("Invalid CPF", {"cpf": message}, self.survey_type)
                    return False, "", "❌ CPF inválido. Por favor, digite um CPF válido com 11 dígitos (apenas números)."
                cpf_limpo = re.sub(r'[^0-9]', '', message)
                formatted_cpf = f"{cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-{cpf_limpo[9:]}"
                return True, cpf_limpo, f"✔️ CPF registrado: {formatted_cpf}"
            if message:
                return True, message, f"✔️ Resposta registrada: {message}"
        return False, "", ""

    def _get_next_question(self, current_question: Dict, selected_answer: str) -> Optional[Dict]:
        """Determines the next question based on the current question and answer."""
        current_index = next(
            (i for i, q in enumerate(self.questions) if str(q["id"]) == str(current_question["id"])),
            -1
        )
        if current_index == -1:
            log_event("Current question index not found", {"current_id": current_question["id"]}, self.survey_type)
            return None

        log_event("Searching for next question", {"current_index": current_index, "current_id": current_question["id"]}, self.survey_type)
        # Check for conditional questions first
        for i, q in enumerate(self.questions[current_index + 1:], start=current_index + 1):
            log_event("Checking question", {"index": i, "id": q["id"], "type": q["type"], "condition": q.get("condition"), "options": q.get("options")}, self.survey_type)
            if q.get("condition") and normalize_text(q["condition"]).lower() == normalize_text(selected_answer).lower():
                log_event("Next question found by condition", {"next_id": q["id"]}, self.survey_type)
                return q

        # Fall back to the next non-conditional question
        for i, q in enumerate(self.questions[current_index + 1:], start=current_index + 1):
            log_event("Checking non-conditional question", {"index": i, "id": q["id"], "type": q["type"], "options": q.get("options")}, self.survey_type)
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
                save_user_state(self.phone, self.campaign_id, None, {})
                message = "começar"

            # Handle survey initiation
            if not current_step or message.lower() in ["participar", "começar", "assinar"]:
                next_question = self.questions[0]
                save_user_state(self.phone, self.campaign_id, next_question["id"], answers)
                if next_question["type"] in ["quick_reply", "multiple_choice"]:
                    options = next_question.get("options", [])
                    if len(options) <= 3:
                        # Use button message for 3 or fewer options
                        buttons = [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": f"opt_{i}",
                                    "title": opt[:20]  # WhatsApp button titles limited to 20 characters
                                }
                            }
                            for i, opt in enumerate(options)
                        ]
                        return {
                            "next_message": {
                                "type": "interactive",
                                "interactive": {
                                    "type": "button",
                                    "body": {
                                        "text": next_question["text"]
                                    },
                                    "action": {
                                        "buttons": buttons
                                    }
                                }
                            }
                        }
                    else:
                        # Use list message for more than 3 options
                        return {
                            "next_message": {
                                "type": "interactive",
                                "interactive": {
                                    "type": "list",
                                    "body": {
                                        "text": next_question["text"]
                                    },
                                    "action": {
                                        "button": "Escolha uma opção",
                                        "sections": [
                                            {
                                                "title": "Opções",
                                                "rows": [
                                                    {
                                                        "id": f"opt_{i}",
                                                        "title": opt[:24],  # WhatsApp list titles limited to 24 characters
                                                        "description": ""
                                                    }
                                                    for i, opt in enumerate(options)
                                                ]
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                else:
                    # Non-interactive question
                    message_text = next_question["text"]
                    log_event("Survey started", {"first_question_id": next_question["id"]}, self.survey_type)
                    return {"next_message": message_text}

            # Find current question
            current_question = next(
                (q for q in self.questions if str(q["id"]) == str(current_step)),
                None
            )
            if not current_question and answers:
                ids_respondidas = sorted([int(k) for k in answers.keys()])
                ultima_id = ids_respondidas[-1]
                current_question = next(
                    (q for q in self.questions if int(q["id"]) == ultima_id),
                    None
                )
            if not current_question:
                log_event("Current question not found", {"current_step": current_step}, self.survey_type)
                return {"next_message": "Erro interno: pergunta atual não encontrada."}

            # Validate answer
            valid_answer, selected_answer, confirmation_text = self._validate_answer(current_question, message)
            if not valid_answer:
                if current_question["type"] in ["quick_reply", "multiple_choice"]:
                    options = current_question.get("options", [])
                    if len(options) <= 3:
                        # Return button message for invalid answer
                        buttons = [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": f"opt_{i}",
                                    "title": opt[:20]
                                }
                            }
                            for i, opt in enumerate(options)
                        ]
                        return {
                            "next_message": {
                                "type": "interactive",
                                "interactive": {
                                    "type": "button",
                                    "body": {
                                        "text": f"❌ Resposta inválida. Escolha uma das opções abaixo:\n{current_question['text']}"
                                    },
                                    "action": {
                                        "buttons": buttons
                                    }
                                }
                            }
                        }
                    else:
                        # Return list message for invalid answer
                        return {
                            "next_message": {
                                "type": "interactive",
                                "interactive": {
                                    "type": "list",
                                    "body": {
                                        "text": f"❌ Resposta inválida. Escolha uma das opções abaixo:\n{current_question['text']}"
                                    },
                                    "action": {
                                        "button": "Escolha uma opção",
                                        "sections": [
                                            {
                                                "title": "Opções",
                                                "rows": [
                                                    {
                                                        "id": f"opt_{i}",
                                                        "title": opt[:24],
                                                        "description": ""
                                                    }
                                                    for i, opt in enumerate(options)
                                                ]
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                else:
                    # Non-interactive invalid answer
                    message_text = f"❌ Resposta inválida. Por favor, responda novamente:\n{current_question['text']}"
                    log_event("Invalid answer", {
                        "question_id": current_question["id"],
                        "answer": message
                    }, self.survey_type)
                    return {"next_message": message_text}

            # Save answer
            answers[str(current_question["id"])] = selected_answer
            save_user_state(self.phone, self.campaign_id, current_question["id"], answers)
            log_event("Answer saved", {
                "question_id": current_question["id"],
                "answer": selected_answer
            }, self.survey_type)

            # Determine next question
            next_question = self._get_next_question(current_question, selected_answer)
            if next_question:
                save_user_state(self.phone, self.campaign_id, next_question["id"], answers)
                if next_question["type"] in ["quick_reply", "multiple_choice"]:
                    options = next_question.get("options", [])
                    if len(options) <= 3:
                        # Use button message for next question
                        buttons = [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": f"opt_{i}",
                                    "title": opt[:20]
                                }
                            }
                            for i, opt in enumerate(options)
                        ]
                        return {
                            "next_message": {
                                "type": "interactive",
                                "interactive": {
                                    "type": "button",
                                    "body": {
                                        "text": f"{confirmation_text}\n\n{next_question['text']}"
                                    },
                                    "action": {
                                        "buttons": buttons
                                    }
                                }
                            }
                        }
                    else:
                        # Use list message for next question
                        return {
                            "next_message": {
                                "type": "interactive",
                                "interactive": {
                                    "type": "list",
                                    "body": {
                                        "text": f"{confirmation_text}\n\n{next_question['text']}"
                                    },
                                    "action": {
                                        "button": "Escolha uma opção",
                                        "sections": [
                                            {
                                                "title": "Opções",
                                                "rows": [
                                                    {
                                                        "id": f"opt_{i}",
                                                        "title": opt[:24],
                                                        "description": ""
                                                    }
                                                    for i, opt in enumerate(options)
                                                ]
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                else:
                    # Non-interactive next question
                    message_text = f"{confirmation_text}\n\n{next_question['text']}"
                    log_event("Moving to next question", {
                        "from": current_question["id"],
                        "to": next_question["id"]
                    }, self.survey_type)
                    return {"next_message": message_text}

            # Survey completion
            save_user_state(self.phone, self.campaign_id, None, answers)
            final_message = self._safe_json_load(self.campaign.get("questions_json", {})).get(
                "outro", "Obrigado por participar da pesquisa!"
            )
            if self.survey_type == "petition" and current_question.get("message"):
                final_message = current_question["message"]
                log_petition_event("Petition completed", {
                    "phone": self.phone,
                    "answers": answers
                })
            log_event("Survey completed", {"answers": answers}, self.survey_type)
            return {"next_message": final_message}

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