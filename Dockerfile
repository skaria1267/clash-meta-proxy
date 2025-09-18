FROM python:3.9-slim

RUN apt-get update && \
    apt-get install -y wget gzip ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080 7890 9090

CMD ["python", "-u", "app.py"]
