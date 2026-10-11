FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV CACHE_DIR=/tmp/cache
EXPOSE 5000
CMD ["python", "-c", "import app; app.app.run(host='0.0.0.0', port=5000)"]
