FROM n8nio/n8n:1.82.3
USER root
RUN apk add --no-cache python3
COPY automation/metrics.py /opt/outreach/metrics.py
USER node
