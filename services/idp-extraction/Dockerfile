FROM python:3.11-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml /workspace/pyproject.toml
COPY packages/idp-contracts /workspace/packages/idp-contracts
COPY packages/idp-common /workspace/packages/idp-common
COPY services/idp-extraction /workspace/services/idp-extraction
WORKDIR /workspace/services/idp-extraction
RUN uv pip install --system -e ../../packages/idp-contracts -e ../../packages/idp-common -e .
EXPOSE 8003
CMD ["python", "-m", "idp_extraction.main"]
