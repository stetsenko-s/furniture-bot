FROM python:3.13-slim
WORKDIR /app
COPY services/sketch/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY contracts /app/contracts
COPY infrastructure /app/infrastructure
COPY services/__init__.py /app/services/__init__.py
COPY services/sketch /app/services/sketch
COPY run_sketch.py /app/run_sketch.py
CMD ["python", "run_sketch.py"]
