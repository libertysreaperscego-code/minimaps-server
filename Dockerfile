FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py session_net.py gunicorn.conf.py calibration.json ./
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
CMD ["gunicorn", "--config", "gunicorn.conf.py", "server:create_app()"]
