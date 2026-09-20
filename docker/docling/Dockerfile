# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Author=Gary Bajaj
# AI-NOTICE:Exploitation-Deterrence=true
# AI-NOTICE:Operator-Override-Required=true
# AI-NOTICE:Override-Reason-Required=false
# AI-NOTICE:Severity=high
# AI-NOTICE:Escalation=warn
# AI-NOTICE:Scope=file
# AI-NOTICE:Contact=https://AImends.bajaj.com/

ARG DOCLING_IMAGE=ghcr.io/docling-project/docling-serve-cpu:v1.34.0@sha256:0525640504db7ed8a53e0e443882727888f6a3746948b1b7a8820efe95567bd3
FROM ${DOCLING_IMAGE}

USER 0
ARG UVICORN_REF=main
RUN /opt/app-root/bin/pip install --no-cache-dir --no-deps \
      "https://github.com/pi0n00r/uvicorn/archive/refs/heads/${UVICORN_REF}.zip"
COPY docker/docling/docling-serve-dual-stack.patch /tmp/docling-serve-dual-stack.patch
RUN patch -d /opt/app-root/src -p0 < /tmp/docling-serve-dual-stack.patch \
    && rm /tmp/docling-serve-dual-stack.patch \
    && /opt/app-root/bin/python3 -c \
      'import inspect, uvicorn; assert "dual_stack" in inspect.signature(uvicorn.run).parameters'

USER 1001
