import httpx
from tqdm import tqdm
import io
import numpy as np
from pydub import AudioSegment
import logging

logging.basicConfig(level=logging.INFO)

import os, sys
sys.path.insert(0, os.path.abspath('CosyVoice'))
sys.path.insert(0, os.path.abspath('CosyVoice/third_party/Matcha-TTS'))
from cosyvoice.cli.cosyvoice import AutoModel  # type: ignore
cosyvoice = AutoModel(model_dir='pretrained_models/Fun-CosyVoice3-0.5B')


def build_wav(text: str):
    print(f"\nStarting streaming synthesis -> {text}")
    # tts_generator = cosyvoice.inference_cross_lingual(
    #     f'You are a helpful assistant.<|endofprompt|>{text}', 
    #     './assets/girl.wav', stream=True, speed=2.0
    # )
    tts_generator = cosyvoice.inference_instruct2(
        text, 
        'You are a helpful assistant. Speak in a fast but clear manner.<|endofprompt|>', 
        './assets/girl.wav', 
        stream=True,
        speed=2.0
    )
    
    for i, chunk_dict in enumerate(tts_generator):
        tts_tensor = chunk_dict['tts_speech']
        
        audio_numpy = (tts_tensor.squeeze().numpy() * 32768).astype(np.int16)
        
        segment = AudioSegment(
            audio_numpy.tobytes(), 
            frame_rate=22050,
            sample_width=2, 
            channels=1
        )
        
        mp3_io = io.BytesIO()
        segment.export(mp3_io, format="mp3", bitrate="128k")
        mp3_bytes = mp3_io.getvalue()
        
        yield mp3_bytes

if __name__ == "__main__":
    texts = {
        "i_am_listening": "我在听。",
        "i_am_thinking": "让我想想哈~",
        "am_i_clear": "你明白了吗？",
        "bye": "后会有期~"
    }
    for action, text in tqdm(texts.items()):
        wav_generator = build_wav(text)
        with open(f"assets/{action}.wav", "wb") as f:
            for wav in wav_generator:
                f.write(wav)