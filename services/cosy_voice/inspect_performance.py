import os, sys
import torchaudio
import time
sys.path.insert(0, os.path.abspath('CosyVoice'))
sys.path.insert(0, os.path.abspath('CosyVoice/third_party/Matcha-TTS'))
from cosyvoice.cli.cosyvoice import AutoModel  # type: ignore

cosyvoice = AutoModel(model_dir='pretrained_models/Fun-CosyVoice3-0.5B')
print(f"✓ 模型加载成功, 采样率: {cosyvoice.sample_rate}Hz")

test_text = 'You are a helpful assistant.<|endofprompt|>八百标兵奔北坡，北坡炮兵并排跑，炮兵怕把标兵碰，标兵怕碰炮兵炮。'
prompt_text = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'
prompt_wav = './assets/girl.wav'

print(f"  合成文本: {test_text}")
print(f"  提示文本: {prompt_text}")
print(f"  提示音频: {prompt_wav}")

start_time = time.time()
for i, result in enumerate(cosyvoice.inference_cross_lingual(test_text, prompt_wav, stream=False, speed=1.5)):
    output_path = f'zero_shot_{i}.wav'
    torchaudio.save(output_path, result['tts_speech'], cosyvoice.sample_rate)
    duration = result['tts_speech'].shape[1] / cosyvoice.sample_rate
    print(f"  ✓ 生成音频 [{i}]: {output_path} (时长: {duration:.2f}s)")

elapsed = time.time() - start_time
print(f"  总耗时: {elapsed:.2f}s")
