FROM python:3.11-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml /workspace/pyproject.toml
COPY packages/idp-contracts /workspace/packages/idp-contracts
COPY packages/idp-common /workspace/packages/idp-common
COPY services/idp-app /workspace/services/idp-app
WORKDIR /workspace/services/idp-app
RUN uv pip install --system -e ../../packages/idp-contracts -e ../../packages/idp-common -e .
EXPOSE 8000
CMD ["python", "-m", "idp_app.main"]
