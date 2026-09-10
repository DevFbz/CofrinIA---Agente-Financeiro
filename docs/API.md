# Referência da API

A API é servida pelo FastAPI. Em produção, a aplicação e a Evolution ficam atrás do proxy reverso; PostgreSQL, Redis, portas internas e a ponte Hermes não devem ser expostos diretamente à internet.

## Autenticação interna

Endpoints sob `/internal` exigem o header:

```text
X-Internal-Token: <valor configurado no ambiente>
```

O token nunca deve ser colocado neste documento, no Git ou em logs.

## Health e métricas

### `GET /health`

Resposta:

```json
{"status":"ok"}
```

### `GET /metrics`

Retorna métricas agregadas em formato Prometheus, sem telefones, textos ou segredos:

```text
cofrinia_up 1
cofrinia_transactions_total 42
cofrinia_pending_confirmations_total 0
cofrinia_delivery_sent_total 10
cofrinia_delivery_failed_total 0
```

## Processamento direto

### `POST /api/process`

Uso local e testes controlados. Não substitui o webhook da Evolution.

```json
{
  "phone": "5511999999999",
  "message": "Gastei R$ 42,50 no almoço via Pix"
}
```

## Webhooks Evolution

### `POST /webhooks/evolution`

### `POST /webhooks/evolution/{event_name}`

A rota específica recebe eventos como `messages-upsert` e `connection-update`.

O webhook:

1. valida o segredo configurado;
2. ignora grupos e mensagens próprias;
3. identifica texto, áudio ou imagem;
4. aplica transcrição/OCR quando necessário;
5. processa a mensagem com idempotência por `message_id`;
6. envia a resposta pela Evolution com retry e chave de entrega.

## Usuários

### `GET /internal/users/active`

Retorna os telefones cadastrados para rotinas internas. Exige `X-Internal-Token`.

## Relatórios

### `GET /internal/reports/monthly?phone=...`

Consulta o resumo mensal sem enviar mensagem.

### `POST /internal/reports/daily/dispatch`

Envia o resumo diário aos usuários cadastrados.

### `POST /internal/reports/weekly/dispatch`

Envia o resumo dos últimos sete dias.

### `POST /internal/reports/monthly/dispatch`

Envia o resumo do mês atual.

Os disparos usam `delivery_records` para impedir duplicidade em reexecuções do n8n.

## Lembretes

### `POST /internal/reminders/process`

Lista lembretes vencidos.

### `POST /internal/reminders/dispatch`

Envia lembretes vencidos e marca os envios concluídos.

### `POST /internal/reminders/{reminder_id}/complete`

Marca um lembrete específico como concluído.

## Recorrências e parcelas

### `POST /internal/recurring/generate`

Gera despesas recorrentes do dia atual, com proteção contra duplicidade.

### `POST /internal/installments/generate`

Gera a parcela correspondente ao dia atual para compras ativas.

## Exportações

### `GET /internal/exports/transactions.csv?phone=...`

Retorna CSV UTF-8 com BOM e delimitador `;`.

### `GET /internal/exports/transactions.xlsx?phone=...`

Retorna workbook Excel com a aba `Lançamentos`.

O filtro por telefone é obrigatório e todas as exportações exigem autenticação interna.

## Ponte Hermes

A aplicação chama a ponte privada configurada em `HERMES_API_URL`:

```text
POST /cofrinia/interpret
```

Payload:

```json
{
  "phone": "5511999999999",
  "message": "relatório de alimentação"
}
```

A ponte retorna JSON estruturado, por exemplo:

```json
{
  "intent": "query_category",
  "amount": null,
  "description": null,
  "category": "alimentacao",
  "payment_method": null,
  "confidence": 0.95,
  "requires_confirmation": false,
  "reply": null
}
```

Hermes nunca grava diretamente no PostgreSQL.

## Respostas e erros

- `200`: operação processada;
- `401`: token ou assinatura inválida;
- `400`: dados de entrada inválidos;
- `502`: falha na ponte ou integração externa;
- `500`: erro inesperado; consultar logs sem expor conteúdo ao usuário.
