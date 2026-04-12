import io
from typing import Literal
import numpy as np
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydub import AudioSegment
import uvicorn

import os, sys
sys.path.insert(0, os.path.abspath('CosyVoice'))
sys.path.insert(0, os.path.abspath('CosyVoice/third_party/Matcha-TTS'))
from cosyvoice.cli.cosyvoice import AutoModel  # type: ignore

app = FastAPI(title="CosyVoice Streaming API")

# cosyvoice = AutoModel('pretrained_models/CosyVoice-300M-SFT')
cosyvoice = AutoModel(model_dir='pretrained_models/Fun-CosyVoice3-0.5B')

class TTSRequest(BaseModel):
    text: str
    speaker: str = "中文女"
    stream: bool = True

@app.post("/api/tts")
async def generate_voice(req: TTSRequest):
    
    def audio_stream_generator():
        print(f"\nStarting streaming synthesis -> {req.text}")
        tts_generator = cosyvoice.inference_cross_lingual(
            f'You are a helpful assistant.<|endofprompt|>{req.text}', 
            './assets/girl.wav', stream=True, speed=2.0
        )
        # tts_generator = cosyvoice.inference_sft(req.text, req.speaker, stream=True)
        
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
            
    return StreamingResponse(audio_stream_generator(), media_type="audio/mpeg")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=50000)