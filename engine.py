from supabase_client import get_campaign, get_user_state, save_user_state

async def process_message(phone: str, campaign_id: str, message: str) -> str:
    campaign = get_campaign(campaign_id)
    print("🔎 Campanha recebida:", campaign)

    if not campaign:
        return "Erro ao carregar campanha."

    flow = campaign.get("flow_json")
    print("🧪 Conteúdo de flow_json:", flow)

    if not flow or not isinstance(flow, dict) or "questions" not in flow:
        return "Campanha sem perguntas definidas."

    questions = flow["questions"]
    user_state = get_user_state(phone, campaign_id)

    current_step = user_state.get("current_step")
    answers = user_state.get("answers", {})

    next_question = None

    if not current_step:
        next_question = questions[0]
    else:
        last_q = next((q for q in questions if str(q["id"]) == str(current_step)), None)
        if last_q:
            answers[str(last_q["id"])] = message
            condition_match = None
            for q in questions:
                if q.get("condition") and q.get("condition") == message:
                    condition_match = q
                    break
            if condition_match:
                next_question = condition_match
            else:
                current_idx = questions.index(last_q)
                if current_idx + 1 < len(questions):
                    next_question = questions[current_idx + 1]

    if not next_question:
        save_user_state(phone, campaign_id, current_step, answers)
        return "Obrigado por participar da pesquisa!"

    save_user_state(phone, campaign_id, next_question["id"], answers)
    return next_question["text"]
