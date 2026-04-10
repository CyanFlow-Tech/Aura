import uuid
from fastapi import FastAPI, Response, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import xml.sax.saxutils as saxutils
import uvicorn
import datetime
import wave
import re
import os
import json
import httpx
os.environ['HF_ENDPOINT'] = "https://hf-mirror.com"
import site
import ctypes

# ==========================================
# 🚨 魔法补丁 2.0：物理级暴力注入动态库
# 必须放在 from faster_whisper import WhisperModel 之前！
# ==========================================
print("\n[系统启动] 正在进行动态库底层注入...")
try:
    site_packages = site.getsitepackages()[0]
    
    # 精确找到你要的两个 .so 物理文件
    cublas_path = os.path.join(site_packages, "nvidia", "cublas", "lib", "libcublas.so.12")
    # 注意：这里必须是 libcudnn.so.8，因为我们降级到了 8.9.2.26
    cudnn_path = os.path.join(site_packages, "nvidia", "cudnn", "lib", "libcudnn.so.8") 
    
    # 使用 ctypes.CDLL 暴力加载进全局内存 (RTLD_GLOBAL 是关键)
    ctypes.CDLL(cublas_path, mode=ctypes.RTLD_GLOBAL)
    ctypes.CDLL(cudnn_path, mode=ctypes.RTLD_GLOBAL)
    print("✅ 成功暴力注入 CUDA 12 & cuDNN 8 动态库！Docker 限制已被击穿。")
except Exception as e:
    print(f"❌ 注入失败，请检查 pip 依赖是否安装正确: {e}")
from faster_whisper import WhisperModel

import edge_tts
import io
# ==========================================
# 🔊 TTS 配置：选择一个好听的中文男声/女声
# ==========================================
# 推荐音色：晓晓 (zh-CN-XiaoxiaoNeural) 或 云希 (zh-CN-YunxiNeural)
VOICE = "zh-CN-YunxiNeural"
async def text_to_speech_bytes(text: str) -> bytes:
    """将文字转为 MP3 字节流"""
    # 1. 过滤非法 Markdown 字符
    text = text.replace("*", "").replace("#", "").replace("`", "").strip()
    
    # 2. 将特殊符号转义，防止破坏 SSML 格式 (比如把 < 变成 &lt;)
    text = saxutils.escape(text)

    if not text:
        text = "我不知道该说什么。"

    communicate = edge_tts.Communicate(text, VOICE)
    audio_data = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk.get("data", b"")
            
    return audio_data



app = FastAPI(title="Aura Edge Relay")
# ==========================================
# 🧠 1. 初始化听觉皮层 (常驻 3090 显存)
# ==========================================
print("\n[系统启动] 正在加载 Whisper 模型到 RTX 3090...")
# 使用 turbo 模型，兼顾极致速度和最高精度。compute_type="float16" 完美契合 3090
asr_model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
print("[系统启动] 听觉皮层加载完毕！等待 Aura 呼叫...\n")


# ==========================================
# 🧠 大模型配置区
# ==========================================
OLLAMA_API_URL = "http://192.168.1.172:11434/api/chat" 
MODEL_NAME = "gemma4:31b"

# 拦截器：遇到这些符号就触发一次 TTS 合成
PUNCTUATIONS = set("，。！？；,.!?;")

# 定义接收的数据格式
class MockRequest(BaseModel):
    client_id: str
    message: str


async def generate_tts_stream(user_text: str):
    """大模型流式推理 + TTS 流式合成的完美流水线"""
    
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "你是 Aura，请简明扼要作答，口语化，不要废话。"},
            {"role": "user", "content": user_text}
        ],
        "stream": True,  # 🚨 开启大模型流式输出
        "think": False
    }

    sentence_buffer = ""

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", OLLAMA_API_URL, json=payload) as response:
            async for chunk in response.aiter_lines():
                if not chunk: continue
                try:
                    data = json.loads(chunk)
                    token = data.get("message", {}).get("content", "")
                    if not token: continue
                    
                    sentence_buffer += token

                    # 🚨 核心流式切分：遇到标点，立刻拿去合成声音！
                    if any(p in token for p in PUNCTUATIONS):
                        clean_sentence = re.sub(r'[*#`_~]', '', sentence_buffer).strip()
                        sentence_buffer = "" # 清空池子装下一句
                        
                        if clean_sentence:
                            print(f"[🔪 句子切分] -> {clean_sentence}")
                            # 🚨 调用 edge_tts 原生 stream 方法！
                            communicate = edge_tts.Communicate(clean_sentence, VOICE)
                            async for audio_chunk in communicate.stream():
                                if audio_chunk["type"] == "audio":
                                    yield audio_chunk.get("data", b"") # 源源不断地喷出二进制音频
                except Exception as e:
                    pass

    # 处理最后一句没有标点结尾的话
    final_sentence = re.sub(r'[*#`_~]', '', sentence_buffer).strip()
    if final_sentence:
        print(f"[🔪 结尾切分] -> {final_sentence}")
        communicate = edge_tts.Communicate(final_sentence, VOICE)
        async for audio_chunk in communicate.stream():
            if audio_chunk["type"] == "audio":
                yield audio_chunk.get("data", b"")

@app.post("/api/aura/upload")
async def upload_audio(audio_file: UploadFile = File(...)):
    task_id = uuid.uuid4().hex
    save_path = f"command_{task_id}.wav"
    
    pcm_data = await audio_file.read()
    with wave.open(save_path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_data)

    return {"status": "success", "task_id": task_id}

@app.get("/api/aura/stream/{task_id}.mp3")
async def stream_audio(task_id: str):
    save_path = f"command_{task_id}.wav"
    
    # 🚨 检查文件是否存在，如果不存在直接报错，避免崩溃
    if not os.path.exists(save_path):
        print(f"[❌ 严重错误] 找不到文件: {save_path}")
        return Response(status_code=404)

    try:
        segments, info = asr_model.transcribe(
            save_path, language="zh", beam_size=5, initial_prompt="以下是一段简体中文。"
        )
        user_text = "".join([segment.text for segment in segments]).strip()
        print(f"[💡 提取指令] 清川说: {user_text}")

        # 🚨 识别为空时给一个默认回复
        if not user_text:
            user_text = "我没听清，请再说一遍。"

        # 🚨 这里的 StreamingResponse 内部会调用 generate_tts_stream
        # 移除函数内的 os.remove(save_path)，文件等以后统一删
        return StreamingResponse(
            generate_tts_stream(user_text), 
            media_type="audio/mpeg"
        )
    except Exception as e:
        print(f"[❌ ASR 链路异常] {e}")
        return Response(status_code=500)
    
@app.post("/api/aura/audio")
async def receive_audio(audio_file: UploadFile = File(...)):
    time_now = datetime.datetime.now().strftime("%H:%M:%S")
    
    # 1. 读取原始的 PCM 字节流
    pcm_data = await audio_file.read()
    print(f"\n[🎤 收到音频载荷 {time_now}] 大小: {len(pcm_data)} bytes")
    
    # 2. 给他加上标准的 WAV 格式头并保存
    save_path = "received_command.wav" # 后缀改成 .wav
    
    with wave.open(save_path, "wb") as wav_file:
        wav_file.setnchannels(1)       # 单声道 (Mono)
        wav_file.setsampwidth(2)       # 16-bit 是 2 个字节
        wav_file.setframerate(16000)   # 16kHz 采样率
        wav_file.writeframes(pcm_data) # 写入裸数据
    
    # ==========================================
    # 🧠 2. ASR 语音转文字
    # ==========================================
    # 强制指定 language="zh" 能进一步压榨识别速度，减少语言探测耗时
    segments, info = asr_model.transcribe(
        save_path, language="zh", beam_size=5, initial_prompt="以下是一段简体中文的对话。"
    )
    
    user_text = "".join([segment.text for segment in segments]).strip()
    
    print(f"[💡 提取指令] 清川说: {user_text}")

    if not user_text:
        #  return {"status": "success", "router_reply": "我没有听清，可以再说一遍吗？"}
        user_text = "我没有听清，可以再说一遍吗？"


    print("[🧠 思考与发声并行中...]")
    
    # 2. 🚨 使用 FastAPI 原生的 StreamingResponse，像视频网站一样返回数据流
    return StreamingResponse(
        generate_tts_stream(user_text), 
        media_type="audio/mpeg"
    )

@app.post("/api/aura/ping")
async def receive_ping(req: MockRequest):
    time_now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"\n[⚡ 神经连接成功 {time_now}]")
    print(f"收到来自 {req.client_id} 的信号: {req.message}")
    
    # 模拟大模型或 Relay 的思考后返回
    return {
        "status": "success", 
        "relay_reply": f"3090 大脑已收到指令！你发送的内容是: {req.message}"
    }

if __name__ == "__main__":
    # 注意：必须用 0.0.0.0，这样局域网内的手机才能访问到
    uvicorn.run(app, host="0.0.0.0", port=8000, loop="asyncio")