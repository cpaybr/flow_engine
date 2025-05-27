<?php
// webhook.php

require_once 'config.php';

// Função para gravar logs estruturados em JSON
function customLog($message, $data = [], $isTest = false) {
    if ($isTest) return;
    $log_file = __DIR__ . '/webhook_log.txt';
    $timestamp = date('Y-m-d H:i:s');
    $log_entry = [
        'timestamp' => $timestamp,
        'message' => $message,
        'data' => $data
    ];
    file_put_contents($log_file, json_encode($log_entry) . "\n", FILE_APPEND);
}

// Função para gravar logs detalhados em survey_tracking_log.txt
function writeSurveyTrackingLog($phoneNumber, $campaignId, $phoneNumberId, $eventType, $details, $isTest = false) {
    if ($isTest) return;
    $timestamp = date('Y-m-d H:i:s');
    $log_entry = [
        'timestamp' => $timestamp,
        'phone_number' => $phoneNumber,
        'campaign_id' => $campaignId,
        'phone_number_id' => $phoneNumberId,
        'event_type' => $eventType,
        'details' => $details
    ];
    file_put_contents('survey_tracking_log.txt', json_encode($log_entry) . "\n", FILE_APPEND);
}

// Função para tratar erros de forma centralizada
function handleError($from, $message, $phone_number_id, $campaign_id = null, $isTest = false) {
    sendWhatsAppMessage($from, $message, $phone_number_id, $isTest);
    customLog("Erro: $message", ['phone' => $from, 'campaign_id' => $campaign_id], $isTest);
    writeSurveyTrackingLog($from, $campaign_id, $phone_number_id, 'error', [
        'error_message' => $message
    ], $isTest);
    http_response_code(200);
    exit;
}

// Função para buscar access_token e whatsapp_id do Supabase
function getAccessToken($phone_number_id) {
    $result = supabaseRequest('GET', "/iap_integration_configurations?select=config_data&config_data->>phone_id=eq.$phone_number_id");
    if (!$result || !isset($result[0]['config_data']['access_token']) || !isset($result[0]['config_data']['whatsapp_id'])) {
        customLog("Nenhum access_token ou whatsapp_id encontrado", ['phone_number_id' => $phone_number_id]);
        return null;
    }
    return [
        'access_token' => $result[0]['config_data']['access_token'],
        'whatsapp_id' => $result[0]['config_data']['whatsapp_id']
    ];
}

// Função para enviar mensagem pelo WhatsApp
function sendWhatsAppMessage($to, $messageOrPayload, $phone_number_id, $isTest = false, $isQuickReply = false) {
    $auth = getAccessToken($phone_number_id);
    if (!$auth) {
        customLog("Auth não encontrado", ['phone' => $to, 'phone_number_id' => $phone_number_id], $isTest);
        writeSurveyTrackingLog($to, null, $phone_number_id, 'error', ['error_message' => 'Auth não encontrado'], $isTest);
        return;
    }
    $access_token = $auth['access_token'];

    if ($isQuickReply || (is_array($messageOrPayload) && isset($messageOrPayload['interactive']))) {
        $payload = [
            'messaging_product' => 'whatsapp',
            'to' => $to,
            'type' => 'interactive',
            'interactive' => $messageOrPayload['interactive']
        ];
    } elseif (is_array($messageOrPayload) && isset($messageOrPayload['type']) && $messageOrPayload['type'] === 'template') {
        $payload = $messageOrPayload;
    } else {
        $payload = [
            'messaging_product' => 'whatsapp',
            'to' => $to,
            'type' => 'text',
            'text' => ['body' => $messageOrPayload]
        ];
    }

    $options = [
        'http' => [
            'header' => "Content-Type: application/json\r\nAuthorization: Bearer $access_token\r\n",
            'method' => 'POST',
            'content' => json_encode($payload)
        ]
    ];

    $context = stream_context_create($options);
    $result = @file_get_contents(WHATSAPP_API_URL . "$phone_number_id/messages", false, $context);
    if ($result === false) {
        customLog("Erro ao enviar mensagem", ['phone' => $to, 'phone_number_id' => $phone_number_id, 'response' => $http_response_header], $isTest);
        writeSurveyTrackingLog($to, null, $phone_number_id, 'error', [
            'error_message' => 'Falha ao enviar mensagem',
            'response' => $http_response_header
        ], $isTest);
    } else {
        customLog("Mensagem enviada", ['phone' => $to, 'phone_number_id' => $phone_number_id, 'payload' => $payload], $isTest);
        writeSurveyTrackingLog($to, null, $phone_number_id, 'message_sent', [
            'message_type' => $payload['type'],
            'content' => $payload[$payload['type']]
        ], $isTest);
    }
}

// Função para verificar se o usuário já participou
function hasParticipated($phone, $campaign_id, $isTest = false) {
    if ($isTest) return false;
    $result = supabaseRequest('GET', "/iap_survey_results?phone_number=eq.$phone&campaign_id=eq.$campaign_id&select=id,completed");
    if ($result && !empty($result) && isset($result[0]['completed']) && $result[0]['completed'] === true) {
        customLog("Usuário já participou", ['phone' => $phone, 'campaign_id' => $campaign_id], $isTest);
        writeSurveyTrackingLog($phone, $campaign_id, null, 'participation_blocked', [], $isTest);
        return true;
    }
    return false;
}

// Função para fazer requisições ao Supabase
function supabaseRequest($method, $endpoint, $data = null) {
    $url = SUPABASE_URL . '/rest/v1' . $endpoint;
    $headers = [
        "Content-Type: application/json",
        "apikey: " . SUPABASE_KEY,
        "Authorization: Bearer " . SUPABASE_KEY
    ];
    $options = [
        'http' => [
            'method' => $method,
            'header' => implode("\r\n", $headers)
        ]
    ];
    if ($data) {
        $options['http']['content'] = json_encode($data);
    }
    $context = stream_context_create($options);
    try {
        $result = @file_get_contents($url, false, $context);
        if ($result === false) {
            customLog("Erro na requisição ao Supabase", ['method' => $method, 'endpoint' => $endpoint, 'response' => $http_response_header]);
            return false;
        }
        return json_decode($result, true);
    } catch (Exception $e) {
        customLog("Exceção na requisição ao Supabase", ['method' => $method, 'endpoint' => $endpoint, 'error' => $e->getMessage()]);
        return false;
    }
}

// Função para carregar campanha
function loadCampaign($identifier, $campaign_id = null) {
    $endpoint = $campaign_id && preg_match('/^[a-f\d]{8}-([a-f\d]{4}-){3}[a-f\d]{12}$/i', $campaign_id)
        ? "/iap_campaigns?campaign_id=eq.$campaign_id&select=campaign_id,title,phone_number_id,flow_json,questions_json,template_id,integration_id,send_report"
        : "/iap_campaigns?phone_number_id=eq.$identifier&select=campaign_id,title,phone_number_id,flow_json,questions_json,template_id,integration_id,send_report&order=created_at.desc&limit=1";
    $result = supabaseRequest('GET', $endpoint);
    if ($result && !empty($result)) {
        $campaign = $result[0];
        $campaign['questions_json'] = isset($campaign['flow_json']) ? $campaign['flow_json'] : $campaign['questions_json'];
        customLog("Campanha carregada", ['campaign' => $campaign]);
        writeSurveyTrackingLog(null, $campaign_id, $identifier, 'campaign_loaded', ['result' => $campaign]);
        return $campaign;
    }
    customLog("Nenhuma campanha encontrada", ['identifier' => $identifier, 'campaign_id' => $campaign_id]);
    return null;
}

// Função para buscar campaign_id por código
function getCampaignIdByCode($code) {
    $result = supabaseRequest('GET', "/iap_campaign_codes?code=eq.$code&select=campaign_id");
    if ($result && !empty($result)) {
        return $result[0]['campaign_id'];
    }
    return null;
}

// Função para chamar o backend FastAPI
function callFastAPI($phone, $campaign_id, $message, $phone_number_id, $isTest = false) {
    $fastapi_url = "http://localhost:8000/process"; // Ajuste para a URL do seu servidor FastAPI
    $payload = [
        'phone' => $phone,
        'campaign_id' => $campaign_id,
        'message' => $message
    ];
    $options = [
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: application/json\r\n",
            'content' => json_encode($payload)
        ]
    ];
    $context = stream_context_create($options);
    $result = @file_get_contents($fastapi_url, false, $context);
    if ($result === false) {
        customLog("Erro ao chamar FastAPI", ['phone' => $phone, 'campaign_id' => $campaign_id, 'response' => $http_response_header], $isTest);
        writeSurveyTrackingLog($phone, $campaign_id, $phone_number_id, 'error', [
            'error_message' => 'Falha ao chamar FastAPI'
        ], $isTest);
        return null;
    }
    return json_decode($result, true);
}

// Verificar método da requisição
$method = $_SERVER['REQUEST_METHOD'];

if ($method === 'GET') {
    $mode = $_GET['hub_mode'] ?? '';
    $token = $_GET['hub_verify_token'] ?? '';
    $challenge = $_GET['hub_challenge'] ?? '';

    if ($mode === 'subscribe' && $token === VERIFY_TOKEN) {
        echo $challenge;
        customLog("Webhook verificado com sucesso");
        http_response_code(200);
    } else {
        echo "Token inválido";
        customLog("Falha na verificação do Webhook", ['error' => 'Token inválido']);
        writeSurveyTrackingLog(null, null, null, 'error', ['error_message' => 'Token inválido']);
        http_response_code(403);
    }
    exit;
}

if ($method === 'POST') {
    $input = file_get_contents('php://input');
    $data = json_decode($input, true);
    customLog("Payload recebido", ['payload' => $input]);
    
    if (isset($data['entry'][0]['changes'][0]['value']['messages'][0])) {
        $message = $data['entry'][0]['changes'][0]['value']['messages'][0];
        $from = $message['from'];
        $text = strtolower(trim($message['text']['body'] ?? ''));
        $button_text = strtolower(trim($message['button']['text'] ?? $message['interactive']['button_reply']['id'] ?? $message['interactive']['button_reply']['title'] ?? ''));
        $phone_number_id = $data['entry'][0]['changes'][0]['value']['metadata']['phone_number_id'];
        $contact_name = $data['entry'][0]['changes'][0]['value']['contacts'][0]['profile']['name'] ?? '';
        $isTest = isset($message['context']) && strpos($text, 'teste') !== false;

        writeSurveyTrackingLog($from, null, $phone_number_id, 'payload_received', ['payload' => $input], $isTest);
        customLog("Mensagem recebida", ['phone' => $from, 'text' => $text, 'button_text' => $button_text, 'isTest' => $isTest]);

        $input_message = $button_text ?: $text;
        $campaign = loadCampaign($phone_number_id);
        if (!$campaign) {
            handleError($from, "Nenhuma campanha ativa no momento.", $phone_number_id, null, $isTest);
        }

        if (hasParticipated($from, $campaign['campaign_id'], $isTest)) {
            handleError($from, "Você já participou desta pesquisa!", $phone_number_id, $campaign['campaign_id'], $isTest);
        }

        // Lógica para "começar <código>"
        if (preg_match('/^começar\s+([A-Z0-9]+)$/i', $text, $matches)) {
            $campaign_code = strtoupper($matches[1]);
            $campaign_id = getCampaignIdByCode($campaign_code);
            if ($campaign_id) {
                $campaign = loadCampaign($campaign_id, $campaign_id);
                if ($campaign) {
                    if (hasParticipated($from, $campaign['campaign_id'], $isTest)) {
                        handleError($from, "Você já participou desta pesquisa!", $phone_number_id, $campaign['campaign_id'], $isTest);
                    }
                    $response = callFastAPI($from, $campaign['campaign_id'], 'começar', $phone_number_id, $isTest);
                    if ($response && isset($response['next_message'])) {
                        $isQuickReply = isset($campaign['questions_json']['questions'][0]['type']) && 
                                       $campaign['questions_json']['questions'][0]['type'] === 'quick_reply' && 
                                       count($campaign['questions_json']['questions'][0]['options']) <= 3;
                        sendWhatsAppMessage($from, $response['next_message'], $phone_number_id, $isTest, $isQuickReply);
                        writeSurveyTrackingLog($from, $campaign['campaign_id'], $phone_number_id, 'question_sent', [
                            'message' => $response['next_message']
                        ], $isTest);
                    } else {
                        handleError($from, "Erro ao iniciar a campanha. Tente novamente.", $phone_number_id, $campaign['campaign_id'], $isTest);
                    }
                } else {
                    handleError($from, "Código de campanha inválido: $campaign_code.", $phone_number_id, null, $isTest);
                }
                exit;
            }
        }

        // Processar mensagem normal
        $response = callFastAPI($from, $campaign['campaign_id'], $input_message, $phone_number_id, $isTest);
        if ($response && isset($response['next_message'])) {
            $isQuickReply = isset($campaign['questions_json']['questions'][0]['type']) && 
                           $campaign['questions_json']['questions'][0]['type'] === 'quick_reply' && 
                           count($campaign['questions_json']['questions'][0]['options']) <= 3;
            sendWhatsAppMessage($from, $response['next_message'], $phone_number_id, $isTest, $isQuickReply);
            writeSurveyTrackingLog($from, $campaign['campaign_id'], $phone_number_id, 'question_sent', [
                'message' => $response['next_message']
            ], $isTest);
        } elseif (isset($response['detail'])) {
            handleError($from, "Erro ao processar sua resposta. Tente novamente.", $phone_number_id, $campaign['campaign_id'], $isTest);
        }
        http_response_code(200);
        exit;
    }
    customLog("Nenhuma mensagem válida no payload");
    http_response_code(200);
    exit;
}
?>