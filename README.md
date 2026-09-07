# Sprint 0 - estrutura
app/
  api/              Rotas HTTP
  application/      LangGraph e casos de uso
  core/             Configuração e segurança
  domain/           Regras financeiras puras
  integrations/     Evolution API, Hermes e provedores externos
  services/         Serviços de aplicação
  db/               Modelos e repositórios PostgreSQL
tests/
deploy/
  Caddyfile
  docker-compose.yml
scripts/

# Execução local
uv sync --group dev
uv run python -m pytest -q
uv run uvicorn app.main:app --reload

# Execução em produção
cp .env.production.example .env
# substitua todos os CHANGE_ME por segredos reais
docker compose up -d --build

# Verificação
curl http://localhost/health
