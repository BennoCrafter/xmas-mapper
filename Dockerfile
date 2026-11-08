FROM python:3.12-slim
# libgl/glib for opencv, bluez client libs come via dbus from the host
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV XMAS_DATA=/data
EXPOSE 8000
CMD ["python", "main.py", "ui", "--host", "0.0.0.0"]
