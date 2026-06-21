FROM python:3.13-slim

WORKDIR /usr/src/app
COPY requirements.txt .
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "-m", "bot"]
