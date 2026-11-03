"""Single fixed text model, loopback-only OpenAI-compatible JSON/SSE server.

Run with the repository's isolated .venv-mlx. Does not load PyTorch LoRA, fetch
models, accept image URLs/tools, or select model paths from HTTP requests.
"""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import time
from uuid import uuid4


def validate_request(payload, model_name, max_output):
    if not isinstance(payload, dict) or payload.get('model') != model_name:
        raise ValueError('unknown_model')
    allowed = {'model', 'messages', 'max_tokens', 'max_completion_tokens', 'stream', 'temperature', 'n'}
    if set(payload) - allowed or type(payload.get('stream', False)) is not bool or payload.get('n', 1) != 1:
        raise ValueError('unsupported_request')
    if payload.get('temperature', 0) != 0:
        raise ValueError('deterministic_temperature_only')
    if 'max_tokens' in payload and 'max_completion_tokens' in payload:
        raise ValueError('ambiguous_output_limit')
    maximum = payload.get('max_completion_tokens', payload.get('max_tokens', 512))
    if type(maximum) is not int or not 1 <= maximum <= max_output:
        raise ValueError('output_limit')
    messages = payload.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise ValueError('invalid_messages')
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                or message['role'] not in ('system', 'user', 'assistant')
                or not isinstance(message['content'], str) or len(message['content']) > 20000):
            raise ValueError('text_messages_only')
    return messages, maximum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=Path('data/runtime/models/qwen3.5-9b-mlx-4bit'))
    parser.add_argument('--served-model', default='qwen3.5-9b-mlx-4bit')
    parser.add_argument('--port', type=int, default=5101)
    parser.add_argument('--max-prompt-tokens', type=int, default=4096)
    parser.add_argument('--max-output-tokens', type=int, default=2048)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 128 <= args.max_prompt_tokens <= 4096 or not 1 <= args.max_output_tokens <= 2048:
        raise SystemExit('Invalid local service limits.')
    path = args.model.resolve()
    if not (path/'conversion.json').is_file():
        raise SystemExit('Missing local conversion manifest; no automatic download.')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    mx.set_memory_limit(10 * 1024**3)
    mx.set_cache_limit(256 * 1024**2)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, *_args):
            pass  # Never log source context or query text.

        def respond(self, status, data):
            raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def event(self, data):
            value=data if isinstance(data,str) else json.dumps(data,ensure_ascii=False,allow_nan=False)
            self.wfile.write(('data: '+value+'\n\n').encode());self.wfile.flush()

        def do_GET(self):
            if self.path == '/v1/models':
                self.respond(200, {'object': 'list', 'data': [{'id': args.served_model, 'object': 'model', 'owned_by': 'local-mlx'}]})
            elif self.path == '/health':
                self.respond(200, {'status': 'ready', 'model': args.served_model, 'text_only': True, 'lora': False, 'streaming': True})
            else:
                self.respond(404, {'error': {'code': 'not_found'}})

        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.respond(404, {'error': {'code': 'not_found'}})
                return
            try:
                if self.headers.get_content_type() != 'application/json' or self.headers.get('Origin'):
                    raise ValueError('server_to_server_json_only')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 128 * 1024:
                    raise ValueError('body_limit')
                payload = json.loads(self.rfile.read(length))
                messages, maximum = validate_request(payload, args.served_model, args.max_output_tokens)
                prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
                prompt_ids = tokenizer.encode(prompt)
                if len(prompt_ids) > args.max_prompt_tokens:
                    raise ValueError('prompt_token_limit')
            except (ValueError, TypeError, KeyError):
                self.respond(400, {'error': {'code': 'invalid_or_oversized_text_request'}})
                return
            started = time.monotonic()
            streaming=payload.get('stream',False)
            if streaming:
                self.send_response(200);self.send_header('Content-Type','text/event-stream; charset=utf-8')
                self.send_header('Cache-Control','no-cache');self.send_header('Connection','close');self.end_headers()
                self.close_connection=True
            try:
                pieces, last = [], None
                for item in stream_generate(model, tokenizer, prompt=prompt_ids, max_tokens=maximum,
                                            sampler=make_sampler(temp=0), prefill_step_size=256):
                    pieces.append(item.text)
                    last = item
                    if streaming and item.text:
                        self.event({'model':args.served_model,'choices':[{'index':0,'delta':{'content':item.text},'finish_reason':None}]})
                if last is None:
                    raise RuntimeError('empty_generation')
                if streaming:
                    self.event({'model':args.served_model,'choices':[{'index':0,'delta':{},'finish_reason':last.finish_reason}],
                                'usage':{'prompt_tokens':last.prompt_tokens,'completion_tokens':last.generation_tokens,'total_tokens':last.prompt_tokens+last.generation_tokens}})
                    self.event('[DONE]')
                    return
                self.respond(200, {'id': 'chatcmpl-' + uuid4().hex, 'object': 'chat.completion',
                    'created': int(time.time()), 'model': args.served_model,
                    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': ''.join(pieces)},
                                 'finish_reason': last.finish_reason}],
                    'usage': {'prompt_tokens': last.prompt_tokens, 'completion_tokens': last.generation_tokens,
                              'total_tokens': last.prompt_tokens + last.generation_tokens},
                    'local_metrics': {'elapsed_seconds': round(time.monotonic()-started, 3),
                                      'peak_memory_bytes': mx.get_peak_memory()}})
            except (RuntimeError, ValueError):
                if streaming:self.event({'error':{'code':'local_inference_failed'}})
                else:self.respond(500, {'error': {'code': 'local_inference_failed'}})
            except (BrokenPipeError,ConnectionResetError):
                pass
            finally:
                mx.clear_cache()

    # Bind before model load: an occupied port must not waste memory or stop another service.
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    try:
        model, tokenizer = load(str(path), tokenizer_config={'trust_remote_code': False})
        print(json.dumps({'status': 'ready', 'url': f'http://127.0.0.1:{args.port}/v1/chat/completions',
                          'model': args.served_model, 'active_memory_bytes': mx.get_active_memory()}), flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
