FROM python:3.11-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml /workspace/pyproject.toml
COPY packages/idp-contracts /workspace/packages/idp-contracts
COPY packages/idp-common /workspace/packages/idp-common
COPY services/idp-orchestrator /workspace/services/idp-orchestrator
WORKDIR /workspace/services/idp-orchestrator
RUN uv pip install --system -e ../../packages/idp-contracts -e ../../packages/idp-common -e .
EXPOSE 8001
CMD ["python", "-m", "idp_orchestrator.main"]
