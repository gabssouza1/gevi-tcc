# Imagem de contêiner dos 5 agentes do AgentCore Runtime (tarefa 9.6).
# Contexto de build: esta pasta (sistema/agentcore/). Publicada no ECR por
# scripts/configurar_agentcore.py; o Runtime (AgentsStack) referencia a URI.
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Dependências primeiro (cache de camada).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação (orquestração + servidor HTTP do contrato do Runtime).
COPY agentes.py servidor.py ./

# O AgentCore Runtime invoca o contêiner via HTTP em 8080 (/ping e /invocations).
EXPOSE 8080

# Usuário não-root (boa prática de segurança).
RUN useradd --create-home --uid 10001 agente
USER agente

CMD ["python", "servidor.py"]
