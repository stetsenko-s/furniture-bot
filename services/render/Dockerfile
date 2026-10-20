FROM python:3.13-slim
WORKDIR /app
COPY services/render/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY contracts /app/contracts
COPY infrastructure /app/infrastructure
COPY services/__init__.py /app/services/__init__.py
COPY services/render /app/services/render
COPY run_render.py /app/run_render.py
CMD ["python", "run_render.py"]
