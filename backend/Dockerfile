FROM python:3.12-slim

# Install kubectl — detects host architecture automatically
ARG TARGETARCH=amd64
RUN apt-get update && apt-get install -y curl ca-certificates && \
    KUBECTL_VERSION=$(curl -sSL https://dl.k8s.io/release/stable.txt) && \
    curl -sSLO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" && \
    chmod +x kubectl && mv kubectl /usr/local/bin/ && \
    apt-get purge -y curl && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
