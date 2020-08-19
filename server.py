from sanic.websocket import WebSocketProtocol
from sanic_cors import CORS, cross_origin
from sanic.response import json
from asyncio import sleep
from sanic import Sanic
import ujson as js
import aiohttp
import asyncio
import logging
import dotenv
import queue
import time
import uuid
import sys
import os

# load bot token from .env
env_path = '.env'
dotenv.load_dotenv(env_path)
# Security tokens
SERVER_TOKEN = os.getenv("SERVER_TOKEN")
# Sever url
SERVER_URL = os.getenv("SERVER_URL")
# Webhook
WEBHOOK = os.getenv("WEBHOOK")
# Check if loaded all values correctly
if any(x is None for x in (SERVER_TOKEN, SERVER_URL, WEBHOOK)):
    raise ValueError(
        "You need to fill all variables\n" \
        f"    SERVER_TOKEN: {SERVER_TOKEN}\n" \
        f"    SERVER_URL:   {SERVER_URL}\n" \
        f"    WEBHOOK:      {WEBHOOK}"
        )

app = Sanic("HumanBios-Web")
CORS(app)
cache = dict()
H = {'content-type': 'application/json'}
# global variables that will be filled in setup()
INSTANCE_TOKEN = None
INSTANCE_NAME = None
# default name to use when message is from the service
DEFAULT_NAME = "HumanBios"
# max size of the cached history (page "reload-consistent" messages)
# TODO: use REDIS to make instance's cache "reload-consistent"
MAXSIZE = 15

# websocket to recieve events from client
@app.websocket('/api/messages')
async def serve_messages(request, ws):
    # [DEBUG]
    logging.info(request.cookies)
    # get session from cookies
    session = request.cookies.get('humanbios-session')
    # set websocket to according session
    if session:
        cache[session]["socket"] = ws
        # get name from cookies
        name = request.cookies.get('humanbios-name')
        # get queue of the cache
        q = cache[session]["history"]
    else:
        q = None
        name = None

    while True:
        # recieve new events
        payload = await ws.recv()
        # [DEBUG]
        logging.info(payload)
        # parse to json
        payload = js.loads(payload)
        # depending on the event type -> handle message
        if payload.get("event") == "new_message":
            # remove `event` key to reuse this object
            del payload['event']
            # fill requirements according to the server SCHEMA
            payload.update({
                "user": {
                    "first_name": name,
                    "user_id": session
                },
                "chat": {
                    "chat_id": session
                },
                "service_in": "webchat",
                "security_token": INSTANCE_TOKEN,
                "via_instance": INSTANCE_NAME,
                "has_message": True
            })
            # save message to the history
            # schema:
            #
            #     TYPES:
            #         TEXT: str
            #         URL:  str
            #     
            #     SCHEMA:
            #             user,     message,     buttons,     has_file,     file
            #              |           |            |           |            |
            #             dict        dict      list|None      bool      list|None
            #              |           |         |                           |
            #  "first_name":TEXT "text":TEXT    dict                        dict
            #                                    |                           |
            #                              "text":TEXT              "payload":URL
            q.append({
                "user": {
                    "first_name": "You"
                }, 
                "message": { 
                    "text": payload['message']['text'] 
                },
                "buttons": None,
                "has_file": False,
                "file": None
            })
            # if History is longer than MAXSIZE -> pop oldest
            if len(q) > MAXSIZE:
                q.pop(0)
            # send message to the server
            async with aiohttp.ClientSession() as client:
                await client.post(f"{SERVER_URL}/api/process_message", json=payload, headers=H)
        # if start event
        elif payload.get("event") == "start":
            if not session:
                session = payload.get("session")
                cache[session]["socket"] = ws
                # get name from cookies
                name = request.cookies.get('humanbios-name')
                # get queue of the cache
                q = cache[session]["history"]
            # empty history
            if not q:
                # send `/start` command to trigger conversation
                payload = {
                    "user": {
                        "first_name": name,
                        "user_id": session
                    },
                    "chat": {
                        "chat_id": session
                    },
                    "service_in": "webchat",
                    "security_token": INSTANCE_TOKEN,
                    "via_instance": INSTANCE_NAME,
                    "has_message": True,
                    "message": {
                        "text": "/start"
                    }
                }
                # send data to the server
                async with aiohttp.ClientSession() as client:
                    await client.post(f"{SERVER_URL}/api/process_message", json=payload, headers=H)
            # has cached history
            else:
                # for each message in the history (from oldest to newest)
                for index, each_message in enumerate(q):
                    # if not the newest message -> force remove buttons
                    if index < len(q) - 1:
                        each_message['buttons'] = None
                    # configure event so client will know what to do with it
                    each_message['event'] = "new_message"
                    # send dumped json via socket
                    await ws.send(js.dumps(each_message))

@app.route('/api/webhook/out', methods=['POST'])
async def webhook_from_server(request):
    data = request.json
    # [DEBUG]
    # logging.info(f"Server response: {data}")
    # modify data for the client
    # set event 
    data['event'] = "new_message"
    # if user is talking to the bot and not other user -> set default name
    data['user']['first_name'] = data['user']['first_name'] if data['user']['user_id'] != data['chat']['chat_id'] else DEFAULT_NAME
    # save message to the history
    # get queue for this user
    q = cache[data['chat']['chat_id']]['history']
    # append object according to the schema
    q.append({
        "user": {
            "first_name": data['user']['first_name']
        }, 
        "message": { 
            "text": data['message']['text']
        },
        "buttons": data['buttons'],
        "has_file": data['has_file'],
        "file": data['file']
    })
    # if history is too long -> pop oldest message
    if len(q) > MAXSIZE:
        q.pop(0)
    # send data via corresponding websocket to the user
    await cache[data['chat']['chat_id']]['socket'].send(js.dumps(data))
    # respond to the server according to info/debug style schema
    return json({"status": 200, "timestamp": time.monotonic()})


@app.route('/api/get_session')
async def serve_session(request):
    # get session id from the cookies
    session = request.cookies.get('humanbios-session')
    # if new user OR session is not stored -> new chat
    if session is None or session not in cache:
        # create unique session
        session = str(uuid.uuid4())
        # user list instead of queue.Queue so we can actually iterate over it
        cache[session] = {
            "history": list()
        }
        # status 200 + authorisation confirmed (new session created)
        status = 201
    else:
        # status 200 + no actions taken (existing session served)
        status = 204
    # respond with status and relevant session
    resp = json({"status": status, "session": session})
    # set client cookies session
    # @Important: doesn't work with cross origin requests
    # @Important: front-end app has to create cookies by itself
    resp.cookies['humanbios-session'] = session
    return resp


async def setup():
    global INSTANCE_TOKEN, INSTANCE_NAME
    data = {
        "security_token": SERVER_TOKEN,
        "url": f"{WEBHOOK}/api/webhook/out"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{SERVER_URL}/api/setup", json=data) as response:
            result = await response.json()
            # [INFO]
            logging.info(result)
            if result['status'] == 200:
                INSTANCE_TOKEN = result['token']
                INSTANCE_NAME = result['name']


if __name__ == "__main__":
    if not os.path.exists("log"):
        os.mkdir("log")
    # Logging
    formatter = '%(asctime)s - %(filename)s - %(levelname)s - %(message)s'
    date_format = '%d-%b-%y %H:%M:%S'

    logging.basicConfig(
        format=formatter,
        datefmt=date_format,
        level=logging.INFO
    )

    logging.basicConfig(
        filename=os.path.join("log", "logging.log"),
        filemode="a+",
        format=formatter,
        datefmt=date_format,
        level=logging.ERROR
    )

    asyncio.run(setup())
    app.run(host="0.0.0.0", port=8080, protocol=WebSocketProtocol)
