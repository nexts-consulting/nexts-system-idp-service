FROM python:3.11-slim

WORKDIR /app
RUN pip install uv

COPY pyproject.toml /workspace/pyproject.toml
COPY packages/idp-contracts /workspace/packages/idp-contracts
COPY packages/idp-common /workspace/packages/idp-common
COPY services/idp-preprocess /workspace/services/idp-preprocess

WORKDIR /workspace/services/idp-preprocess
RUN uv pip install --system -e ../../packages/idp-contracts -e ../../packages/idp-common -e .

EXPOSE 8002
CMD ["python", "-m", "idp_preprocess.main"]
