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
    AGENTD_PORT=8080 \
    AGENTD_HOOK_PORT=9000

WORKDIR /opt/agentd

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agentd ./agentd

# 8080: agentd application API (proxied to clients). 9000: lifecycle hooks
# (platform-only). Both must be exposed so the platform can reach the hook port.
EXPOSE 8080 9000

# serve.py starts both listeners in one process: agentd on AGENTD_PORT and the
# lifecycle-hook server on AGENTD_HOOK_PORT. User workloads are launched via exec.
CMD ["python", "-m", "agentd.serve"]
