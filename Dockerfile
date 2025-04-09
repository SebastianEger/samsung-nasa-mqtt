FROM python:3.12

WORKDIR /samsung-nasa-mqtt

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .
ENTRYPOINT ["python", "./samsung_mqtt_home_assistant_mod.py"]