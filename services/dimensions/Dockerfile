FROM python:3.13-slim
WORKDIR /app
COPY services/dimensions/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY contracts /app/contracts
COPY infrastructure /app/infrastructure
COPY services/__init__.py /app/services/__init__.py
COPY services/dimensions /app/services/dimensions
COPY run_dimensions.py /app/run_dimensions.py
CMD ["python", "run_dimensions.py"]
