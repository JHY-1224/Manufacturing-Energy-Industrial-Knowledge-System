"""Read-only source -> separately stored Qwen3.5 text-only MLX 4-bit derivative.

Uses MLX-LM's official qwen3_5 sanitizer and quantizer. Evaluates one leaf at a
time on CPU so a 24 GiB Mac need not materialize the entire BF16 model on Metal.
No training, LoRA merge, network access, or replacement of an existing output.
"""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import resource
import shutil
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('data/runtime/models/qwen3.5-9b-mlx-4bit'))
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists() or source == output or source in output.parents:
        raise SystemExit('Output must be a new directory outside the original model.')
    config = json.loads((source/'config.json').read_text())
    if config.get('model_type') != 'qwen3_5' or config.get('quantization'):
        raise SystemExit('Expected the original unquantized Qwen3.5 checkpoint.')
    index_path = source/'model.safetensors.index.json'
    index = json.loads(index_path.read_text())
    names = sorted(set(index['weight_map'].values()))
    before = {name: {'bytes': (source/name).stat().st_size, 'mtime_ns': (source/name).stat().st_mtime_ns} for name in names}
    if shutil.disk_usage(output.parent if output.parent.exists() else Path.cwd()).free < 12 * 1024**3:
        raise SystemExit('At least 12 GiB free disk is required.')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import mlx.core as mx
    from mlx_lm.utils import load, quantize_model, save
    mx.set_default_device(mx.cpu)
    mx.set_cache_limit(64 * 1024**2)
    started = time.monotonic()
    print('Loading lazy text model; original source is read-only.', flush=True)
    model, tokenizer, config = load(str(source), return_config=True, lazy=True,
                                    tokenizer_config={'trust_remote_code': False}, trust_remote_code=False)
    model, config = quantize_model(model, config, group_size=64, bits=4, mode='affine')
    leaves = model.leaf_modules()
    from mlx.utils import tree_flatten
    modules = tree_flatten(leaves, is_leaf=lambda value: hasattr(value, 'parameters'))
    for count, (_name, module) in enumerate(modules, 1):
        mx.eval(module.parameters())
        mx.clear_cache()
        if count % 40 == 0 or count == len(modules):
            print(json.dumps({'evaluated_modules': count, 'total_modules': len(modules),
                              'max_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), flush=True)
    save(output, source, model, tokenizer, config)
    for name, expected in before.items():
        stat = (source/name).stat()
        if stat.st_size != expected['bytes'] or stat.st_mtime_ns != expected['mtime_ns']:
            raise RuntimeError('Original file stat changed during conversion; derivative not approved.')
    converted_index = json.loads((output/'model.safetensors.index.json').read_text())
    if any('visual' in name or 'vision_tower' in name for name in converted_index['weight_map']):
        raise RuntimeError('Unexpected visual tensor in text-only derivative.')
    result = {'status': 'converted_not_yet_inference_verified', 'source_model': 'Qwen/Qwen3.5-9B',
              'source_directory': str(source), 'source_index_sha256': hashlib.sha256(index_path.read_bytes()).hexdigest(),
              'original_weights_stat_unchanged': True, 'source_weights': before,
              'text_only': True, 'lora_applied': False, 'training_performed': False,
              'quantization': {'bits': 4, 'group_size': 64, 'mode': 'affine'},
              'versions': {name: version(name) for name in ('mlx', 'mlx-metal', 'mlx-lm', 'transformers', 'tokenizers')},
              'weights_bytes': sum((output/name).stat().st_size for name in set(converted_index['weight_map'].values())),
              'elapsed_seconds': round(time.monotonic()-started, 3),
              'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'implementation_source': 'https://github.com/ml-explore/mlx-lm/tree/v0.32.2'}
    (output/'conversion.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
