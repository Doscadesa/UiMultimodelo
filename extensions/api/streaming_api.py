import asyncio
import json
from threading import Thread

from websockets.server import serve

from extensions.api.util import build_parameters, try_start_cloudflared
from modules import shared
from modules.chat import generate_chat_reply
from modules.text_generation import generate_reply

PATH = '/api/v1/stream'


async def _handle_connection(websocket, path):

    if path == '/api/v1/stream':
        async for message in websocket:
            message = json.loads(message)

            prompt = message['prompt']
            generate_params = build_parameters(message)
            stopping_strings = generate_params.pop('stopping_strings')
            generate_params['stream'] = True

            generator = generate_reply(
                prompt, generate_params, stopping_strings=stopping_strings, is_chat=False)

            # As we stream, only send the new bytes.
            skip_index = 0
            message_num = 0

            for a in generator:
                to_send = a[skip_index:]
                if to_send is None or chr(0xfffd) in to_send:  # partial unicode character, don't send it yet.
                    continue

                await websocket.send(json.dumps({
                    'event': 'text_stream',
                    'message_num': message_num,
                    'text': to_send
                }))

                await asyncio.sleep(0)
                skip_index += len(to_send)
                message_num += 1

            await websocket.send(json.dumps({
                'event': 'stream_end',
                'message_num': message_num
            }))

    elif path == '/api/v1/chat-stream':
        async for message in websocket:
            body = json.loads(message)

            user_input = body['user_input']
            generate_params = build_parameters(body, chat=True)
            generate_params['stream'] = True
            regenerate = body.get('regenerate', False)
            _continue = body.get('_continue', False)

            generator = generate_chat_reply(
                user_input, generate_params, regenerate=regenerate, _continue=_continue, loading_message=False)

            message_num = 0
            for a in generator:
                await websocket.send(json.dumps({
                    'event': 'text_stream',
                    'message_num': message_num,
                    'history': a
                }))

                await asyncio.sleep(0)
                message_num += 1

            await websocket.send(json.dumps({
                'event': 'stream_end',
                'message_num': message_num
            }))

    else:
        print(f'Streaming api: unknown path: {path}')
        return


async def _run(host: str, port: int):
    async with serve(_handle_connection, host, port, ping_interval=None):
        await asyncio.Future()  # run forever


def _run_server(port: int, share: bool = False):
    address = '0.0.0.0' if shared.args.listen else '127.0.0.1'

    def on_start(public_url: str):
        public_url = public_url.replace('https://', 'wss://')
        print(f'Starting streaming server at public url {public_url}{PATH}')

    if share:
        try:
            try_start_cloudflared(port, max_attempts=3, on_start=on_start)
        except Exception as e:
            print(e)
    else:
        print(f'Starting streaming server at ws://{address}:{port}{PATH}')

    asyncio.run(_run(host=address, port=port))


def start_server(port: int, share: bool = False):
    Thread(target=_run_server, args=[port, share], daemon=True).start()
