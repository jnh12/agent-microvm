# MicroVM image for AWS Lambda MicroVMs.
#
# Lambda MicroVMs runs this Dockerfile, starts the app, and snapshots the fully
# initialized environment. Every launched MicroVM resumes from that snapshot, so
# agentd is already listening the moment run-microvm returns.
#
# Lambda MicroVMs is ARM64-only.
FROM --platform=linux/arm64 python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENTD_PORT=8080

WORKDIR /opt/agentd

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agentd ./agentd

EXPOSE 8080

# agentd is the entrypoint of the sandbox: it exposes exec/fs over HTTP on the
# MicroVM's dedicated endpoint. User workloads are launched via exec calls.
CMD ["uvicorn", "agentd.app:app", "--host", "0.0.0.0", "--port", "8080"]
