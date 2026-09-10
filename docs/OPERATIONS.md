# Operação e deploy

## Componentes

```text
WhatsApp
  ↓
Evolution API
  ↓ webhook
FastAPI / app
  ├── parser financeiro
  ├── OCR de imagens
  ├── faster-whisper local
  ├── fallback Hermes
  └── PostgreSQL

worker
  ├── expiração de confirmações
  ├── geração de recorrências
  └── geração de parcelas
```

O n8n permanece responsável pelos agendamentos e integrações visuais. O backend é a fonte oficial dos dados financeiros.

## Variáveis de ambiente

Nunca copie valores reais para este documento. Os nomes principais são:

```env
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=
APP_DB=
DATABASE_URL=
EVOLUTION_API_URL=
EVOLUTION_API_KEY=
EVOLUTION_INSTANCE=
EVOLUTION_WEBHOOK_SECRET=
COFRIN_INTERNAL_TOKEN=
HERMES_API_URL=
COFRIN_HERMES_TOKEN=
AUDIO_TRANSCRIPTION_PROVIDER=local
WHISPER_MODEL_SIZE=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
WHISPER_MODEL_DIR=/models
```

A imagem Docker define `HF_HUB_DISABLE_XET=1` para evitar falhas no download do modelo em volumes com permissões restritas.

## Comandos de validação

No projeto:

```bash
uv run pytest -q
uv run ruff check app tests
git diff --check
```

Na VM:

```bash
docker compose config -q
docker compose ps
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/metrics
```

## Deploy seguro

1. Criar backup do diretório da aplicação:

```bash
sudo tar -czf "$HOME/finance-whatsapp-backup-$(date +%Y%m%d-%H%M%S).tar.gz" /opt/finance-whatsapp
```

2. Criar backup PostgreSQL:

```bash
sudo docker exec finance-whatsapp-postgres-1 pg_dump -U finance -d finance -Fc > "$HOME/finance-db-backup-$(date +%Y%m%d-%H%M%S).dump"
```

3. Copiar o código para `/opt/finance-whatsapp`.

4. Validar o compose:

```bash
cd /opt/finance-whatsapp
docker compose config -q
```

5. Reconstruir somente os serviços alterados:

```bash
docker compose up -d --build app worker
```

6. Validar:

```bash
curl -fsS http://127.0.0.1:8000/health
docker compose ps
```

PostgreSQL, Redis, Evolution e Caddy não devem ser reiniciados sem necessidade.

## Banco existente

O repositório executa `metadata.create_all` na inicialização. Em bases antigas, confira a existência das tabelas novas antes de usar as funcionalidades:

```text
pending_confirmations
delivery_records
```

A criação deve ser idempotente e nunca deve usar `DROP`, `TRUNCATE` ou recriação de volume em um deploy comum.

## Worker

O worker é um serviço sem porta pública:

```text
python -m app.worker
```

A cada ciclo ele:

- marca confirmações vencidas como `expired`;
- gera recorrências do dia;
- gera parcelas do dia;
- registra o resultado no log sem dados financeiros sensíveis.

O intervalo padrão é de 60 segundos e pode ser ajustado por `WORKER_INTERVAL_SECONDS`, respeitando o mínimo de 10 segundos.

## Idempotência e entrega

`processed_messages` impede reprocessar o mesmo evento de entrada.

`delivery_records` controla saídas agendadas e respostas com estados:

```text
sending
sent
failed
```

Uma entrega recente em `sending` não é repetida. Claims antigos podem ser recuperados após 10 minutos. Falhas retornam a estado recuperável para nova tentativa.

## Áudio

O modelo local fica no volume:

```text
whisper_models:/models
```

Para verificar o cache dentro do container:

```bash
docker exec finance-whatsapp-app-1 python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8', download_root='/models'); print('ready')"
```

O primeiro carregamento pode baixar o modelo e demorar. O processo normal roda como usuário não-root; não coloque chaves ou modelos dentro do Git.

## Evolution

Verificar a conexão:

```bash
docker exec finance-whatsapp-app-1 python -c "import os,httpx; r=httpx.get('http://evolution:8080/instance/connectionState/financeiro',headers={'apikey':os.environ['EVOLUTION_API_KEY']},timeout=15); print(r.text)"
```

Estado esperado:

```json
{"instance":{"instanceName":"financeiro","state":"open"}}
```

A API pode retornar `201` ao aceitar uma mensagem e depois emitir `DELIVERY_ACK`. Um `201` isolado não substitui a investigação dos logs quando o usuário não visualiza a mensagem.

## Logs

```bash
docker logs --since 15m finance-whatsapp-app-1
docker logs --since 15m finance-whatsapp-worker-1
docker logs --since 15m finance-whatsapp-evolution-1
```

Nunca publique `.env`, tokens, chaves, conteúdo de mensagens ou dumps do banco.

## Troubleshooting

### Bot não responde

1. Verifique `docker compose ps`.
2. Verifique `/health`.
3. Verifique o estado `open` da Evolution.
4. Confirme eventos `messages-upsert` no log do app.
5. Procure `failed to transcribe`, `failed to send` e `unexpected`.
6. Confirme se o evento tem `fromMe=false` e texto/mídia válido.

### Áudio falha

1. Confirme `AUDIO_TRANSCRIPTION_PROVIDER=local`.
2. Confirme `faster-whisper` instalado.
3. Confirme o modelo carregável em `/models`.
4. Confirme que a mídia foi baixada pela Evolution.
5. Verifique espaço em disco e permissões do volume.

### Lembrete com horário errado

1. Confirme a unidade (`minutos`, `horas` ou `dias`).
2. Confirme que o texto contém `em` ou `daqui a`.
3. Verifique `due_at` no PostgreSQL em UTC.
4. Compare com o horário `America/Sao_Paulo`.
5. Confirme a execução do dispatch e `delivery_records`.

### Relatório duplicado

Consulte a chave lógica em `delivery_records`. Uma mesma combinação de tipo, telefone e período deve ter apenas uma entrega efetiva.
