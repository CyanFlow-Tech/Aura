import soundfile as sf
import sherpa_onnx

def test_model():
    print("1. 正在加载模型...")
    # Python API 是扁平化的，直接传参
    kws = sherpa_onnx.KeywordSpotter(
        tokens="assets/kws_model/tokens.txt",
        encoder="assets/kws_model/encoder-epoch-13-avg-2-chunk-16-left-64.onnx",
        decoder="assets/kws_model/decoder-epoch-13-avg-2-chunk-16-left-64.onnx",
        joiner="assets/kws_model/joiner-epoch-13-avg-2-chunk-16-left-64.onnx",
        keywords_file="assets/kws_model/keywords.txt",
        provider="cpu",
        num_threads=1
    )
    
    stream = kws.create_stream()

    print("2. 正在读取音频...")
    # 读取 16kHz 的 wav 文件
    data, sample_rate = sf.read("/Users/qingchuan/Downloads/小爱同学.wav", dtype="float32")
    if sample_rate != 16000:
        print(f"警告：音频采样率是 {sample_rate}，而不是 16000！这会导致模型变成聋子！")
        return

    print("3. 喂入音频并推理...")
    stream.accept_waveform(sample_rate, data)
    stream.input_finished() # 告诉引擎这段录音结束了
    
    # 注意：Python 端的方法名带有 _stream 后缀
    while kws.is_ready(stream):
        kws.decode_stream(stream)
        
    # 获取结果，Python 端直接返回字符串
    result = kws.get_result(stream)
    
    if result != "":
        print(f"\n🎉 验证成功！模型听到了：【{result}】")
    else:
        print("\n❌ 验证失败：模型跑完了全过程，但什么都没识别出来。")

if __name__ == "__main__":
    test_model()