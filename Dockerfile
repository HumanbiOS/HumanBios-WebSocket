FROM python:3.8-slim-buster

WORKDIR /usr/src/app

COPY requirements.txt .
RUN pip3 install --upgrade pip wheel \
 && pip3 install --no-cache-dir -r requirements.txt

COPY . .

# Launch
CMD python3 ./server.py
