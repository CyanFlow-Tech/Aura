import 'dart:io';
import 'dart:typed_data';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show rootBundle;
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa_onnx;
import 'package:audioplayers/audioplayers.dart';

void main() {
  runApp(const AuraApp());
}

class AuraApp extends StatelessWidget {
  const AuraApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Aura',
      theme: ThemeData(brightness: Brightness.dark, scaffoldBackgroundColor: Colors.black),
      home: const AuraHomePage(),
    );
  }
}

class AuraHomePage extends StatefulWidget {
  const AuraHomePage({super.key});

  @override
  State<AuraHomePage> createState() => _AuraHomePageState();
}

enum AppMode { kws, recording, processing }



class _AuraHomePageState extends State<AuraHomePage> {
  final AudioRecorder _audioRecorder = AudioRecorder();
  final AudioPlayer _audioPlayer = AudioPlayer();
  
  sherpa_onnx.KeywordSpotter? _kws;
  sherpa_onnx.OnlineStream? _onlineStream;

  AppMode _currentMode = AppMode.kws;
  List<int> _pcmBuffer = [];      // 用来存放你说的话的原始字节
  DateTime? _lastLoudTime;        // 记录最后一次大声说话的时间

  
  String _displayText = '正在初始化 Aura 听觉神经...\n首次启动需要释放模型文件，请稍候';
  bool _isListening = false;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              // 根据状态机切换图标
              _currentMode == AppMode.recording ? Icons.mic 
                : _currentMode == AppMode.kws ? Icons.hearing 
                : Icons.api,
              size: 80,
              // 录音时红色，待命时青色，处理请求时灰色
              color: _currentMode == AppMode.recording ? Colors.redAccent 
                : _currentMode == AppMode.kws ? Colors.cyanAccent 
                : Colors.grey,
            ),
            const SizedBox(height: 30),
            Text(
              _displayText,
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold, height: 1.5),
            ),
          ],
        ),
      ),
    );
  }

  @override
  void initState() {
    super.initState();
    _audioPlayer.setAudioContext(AudioContext(
      android: AudioContextAndroid(
        audioFocus: AndroidAudioFocus.none, // 绝不抢占焦点
      ),
      iOS: AudioContextIOS(
        category: AVAudioSessionCategory.playAndRecord,
        options: {
          AVAudioSessionOptions.defaultToSpeaker,
          AVAudioSessionOptions.mixWithOthers, 
        },
      ),
    ));
    _initAura();
    _audioPlayer.onPlayerComplete.listen((event) {
      // 🚨 加上 500ms 延迟，等扬声器物理余音彻底散去
      Future.delayed(const Duration(milliseconds: 500), () {
        if (mounted) {
          setState(() {
            _currentMode = AppMode.kws;
            _displayText = 'Aura 再次就绪\n请试着喊："小爱同学"';
            _pcmBuffer.clear();
            
            // 🚨 回归官方最安全的 C++ 状态清空方法
            if (_kws != null && _onlineStream != null) {
              _kws!.reset(_onlineStream!); 
            }
          });
        }
      });
    });
  }

  void _startRecording() {
    if (!mounted) return;
    setState(() {
      _currentMode = AppMode.recording;
      _displayText = '我在听...\n(请说出你的问题)';
      _pcmBuffer.clear();
      _lastLoudTime = DateTime.now(); // 初始化防呆时间
    });
  }

  Future<void> _stopAndSendAudio() async {
    if (_currentMode != AppMode.recording) return;
    if (!mounted) return;
    setState(() {
      _currentMode = AppMode.processing;
      _displayText = '录音结束，正在发送给大脑...';
    });

    try {
      var request = http.MultipartRequest('POST', Uri.parse('http://192.168.1.114:18000/api/aura/upload'));
      request.files.add(http.MultipartFile.fromBytes('audio_file', _pcmBuffer, filename: 'command.pcm'));

      var response = await request.send();
      
      if (response.statusCode == 200) {
        var responseBody = await response.stream.bytesToString();
        var data = jsonDecode(responseBody);
        String taskId = data['task_id'];

        setState(() {
          _displayText = '正在思考中...';
        });

        await _audioPlayer.stop();
        await _audioPlayer.release();
        String streamUrl = 'http://192.168.1.114:18000/api/aura/stream/$taskId.mp3';
        
        // await _audioPlayer.play(UrlSource(streamUrl));

        await _safeStreamPlay(streamUrl);
      } else {
        setState(() {
          _displayText = '大脑短路了: 状态码 ${response.statusCode}';
        });
        _resetToKws(); // 失败时，手动重置
      }
    } catch (e) {
      setState(() {
        _displayText = '发送失败: $e';
      });
      _resetToKws(); // 失败时，手动重置
    }
  }

  /// 全新的安全流式播放方法
  Future<void> _safeStreamPlay(String url) async {
    // 获取临时目录，用于存放边下边播的 mp3 文件
    final tempDir = await getTemporaryDirectory();
    final tempFile = File('${tempDir.path}/aura_stream_${DateTime.now().millisecondsSinceEpoch}.mp3');
    final sink = tempFile.openWrite();

    try {
      // 发起 GET 请求，拿到底层字节流
      final client = http.Client();
      final request = http.Request('GET', Uri.parse(url));
      final response = await client.send(request);

      // 拦截初始的 404 等非 200 状态码
      if (response.statusCode != 200) {
        setState(() => _displayText = '服务器拒绝请求: ${response.statusCode}');
        _resetToKws();
        return;
      }

      String checkBuffer = ''; // 用于拼接并检查是否有后端传来的错误 JSON
      bool isError = false;

      // 边读流，边检查，边写本地文件
      await for (final chunk in response.stream) {
        if (isError) break; // 如果已经发现错误，停止读取剩余流

        // 1. 将 chunk 当作字符串尝试解析（正常 MP3 是乱码，不影响判断）
        final chunkStr = utf8.decode(chunk, allowMalformed: true);
        checkBuffer += chunkStr;

        // 2. 检查是否包含你在 Python 后端定义的错误标识
        // (对应上文 Python 代码中 yield 的 json.dumps({"type": "stream_error"...}))
        if (checkBuffer.contains('"type":"stream_error"') || checkBuffer.contains('"stream_error"')) {
          debugPrint("⚠️ 拦截到后端流式错误标识！");
          isError = true;
          
          // 立刻停止播放器，关闭文件，斩断死循环
          await _audioPlayer.stop();
          await sink.close();
          // 删掉写了一半的残缺 mp3
          if (await tempFile.exists()) await tempFile.delete();
          
          setState(() => _displayText = '后端生成失败，请检查模型服务');
          _resetToKws();
          return; // 直接退出方法
        }

        // 3. 防止 checkBuffer 内存无限增长（保留最后 100 个字符足够判断了）
        if (checkBuffer.length > 100) {
          checkBuffer = checkBuffer.substring(checkBuffer.length - 100);
        }

        // 4. 如果没有错误，将正常的 MP3 字节写入本地文件
        sink.add(chunk);
      }

      await sink.close(); // 流读取完毕，关闭文件写入

      // 如果没有发生错误，且文件有内容，开始播放本地文件
      if (!isError && await tempFile.exists()) {
        // 🚨 注意这里用的是 DeviceFileSource (本地文件路径)，不再是 UrlSource
        await _audioPlayer.play(DeviceFileSource(tempFile.path));
        
        // 播放结束后，在 onPlayerComplete 监听里清理临时文件
        _audioPlayer.onPlayerComplete.first.whenComplete(() async {
          if (await tempFile.exists()) await tempFile.delete();
        });
      }

    } catch (e) {
      debugPrint("流式下载或播放发生严重网络错误: $e");
      await sink.close();
      if (await tempFile.exists()) await tempFile.delete();
      setState(() => _displayText = '网络连接中断');
      _resetToKws();
    }
  }

  void _resetToKws() {
    Future.delayed(const Duration(seconds: 2), () {
      if (mounted) {
        setState(() {
          _currentMode = AppMode.kws;
          _displayText = 'Aura 再次就绪\n请试着喊："小爱同学"';
          _pcmBuffer.clear();
          
          // 🚨 同样替换为销毁重建逻辑
          if (_kws != null) {
            _onlineStream?.free();
            _onlineStream = _kws!.createStream();
          }
        });
      }
    });
  }
  // 核心辅助方法：将 assets 中的模型拷贝到真机物理目录下，供 C++ 底层调用
  Future<String> _copyAsset(String assetPath) async {
    final directory = await getApplicationDocumentsDirectory();
    final file = File('${directory.path}/$assetPath');

    // if (!await file.exists()) {
    await file.parent.create(recursive: true);
    final data = await rootBundle.load(assetPath);
    final bytes = data.buffer.asUint8List();
    await file.writeAsBytes(bytes, flush: true);
    // }
    return file.path;
  }

  Future<void> _initAura() async {
    // 1. 申请麦克风权限
    final status = await Permission.microphone.request();
    if (status != PermissionStatus.granted) {
      setState(() => _displayText = '需要麦克风权限才能唤醒！');
      return;
    }

    // 2. 初始化底层绑定
    sherpa_onnx.initBindings();

    // 3. 将模型解压到物理路径
    final encoderPath = await _copyAsset('assets/kws_model/encoder-epoch-13-avg-2-chunk-16-left-64.onnx');
    final decoderPath = await _copyAsset('assets/kws_model/decoder-epoch-13-avg-2-chunk-16-left-64.onnx');
    final joinerPath = await _copyAsset('assets/kws_model/joiner-epoch-13-avg-2-chunk-16-left-64.onnx');
    final tokensPath = await _copyAsset('assets/kws_model/tokens.txt');
    final keywordsPath = await _copyAsset('assets/kws_model/keywords.txt');

    // 4. 配置模型
    final transducer = sherpa_onnx.OnlineTransducerModelConfig(
      encoder: encoderPath,
      decoder: decoderPath,
      joiner: joinerPath,
    );
    
    final modelConfig = sherpa_onnx.OnlineModelConfig(
      transducer: transducer,
      tokens: tokensPath,
      debug: true,
    );

    final config = sherpa_onnx.KeywordSpotterConfig(
      model: modelConfig,
      keywordsFile: keywordsPath,
      keywordsThreshold: 0.1,
      keywordsScore: 1.5,
      feat: const sherpa_onnx.FeatureConfig(
        sampleRate: 16000,
        featureDim: 80,
      ),
    );

    // 5. 实例化听觉神经
    _kws = sherpa_onnx.KeywordSpotter(config);
    _onlineStream = _kws!.createStream();

    // 6. 开启麦克风监听音频流
    final stream = await _audioRecorder.startStream(const RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: 16000,
      numChannels: 1,
    ));

    setState(() {
      _displayText = 'Aura 已就绪\n请试着喊："小爱同学"';
      _isListening = true;
    });

    // 7. 处理麦克风流
    stream.listen((data) {
      if (_kws == null || _onlineStream == null) return;

      // 转换为模型认识的浮点格式 Float32List [-1.0, 1.0]
      // final int16List = data.buffer.asInt16List();
      final pcmBytes = Uint8List.fromList(data);
      final byteData = ByteData.sublistView(pcmBytes);
      final sampleCount = pcmBytes.lengthInBytes ~/ 2;
      final float32List = Float32List(sampleCount);
      final int length = data.length;
      final int safeLength = length % 2 == 0 ? length : length - 1;

      double volumeSum = 0;
      for (int i = 0; i < sampleCount; i++) {
        final sample = byteData.getInt16(i * 2, Endian.little);
        float32List[i] = sample / 32768.0;
        volumeSum += float32List[i].abs();
      }
      double currentVolume = volumeSum / float32List.length;

      if (_currentMode == AppMode.kws) {
        _onlineStream!.acceptWaveform(samples: float32List, sampleRate: 16000);
        while (_kws!.isReady(_onlineStream!)) {
          _kws!.decode(_onlineStream!);
          final keyword = _kws!.getResult(_onlineStream!).keyword;
          if (keyword.isNotEmpty) {
            // 🚨 听到唤醒词后，立刻销毁旧流，保证下次的纯净
            _kws!.reset(_onlineStream!);
            _startRecording(); // 切入录音模式
            break;
          }
        }
      }
      // ====== 状态 2：唤醒成功，正在录制指令并进行 VAD 判断 ======
      else if (_currentMode == AppMode.recording) {
        // 把声音字节原封不动塞进我们的袋子里
        _pcmBuffer.addAll(data.sublist(0, safeLength));

        // VAD 核心逻辑：如果音量大于经验阈值 (0.02)，说明你在说话
        if (currentVolume > 0.02) { 
          _lastLoudTime = DateTime.now();
        }

        // 计算静音时长和总录音时长
        final quietDuration = DateTime.now().difference(_lastLoudTime!).inMilliseconds;
        final recordSeconds = _pcmBuffer.length / (16000 * 2); // 16kHz*16bit

        // 停止条件：连续安静超过 1.5 秒，或者总长超过 10 秒（防死锁）
        if (quietDuration > 3000 || recordSeconds > 10.0) {
          _stopAndSendAudio();
        }
      }

    });
  }


  @override
  void dispose() {
    _audioRecorder.dispose();
    _audioPlayer.dispose();
    _onlineStream?.free();
    _kws?.free();
    super.dispose();
  }

}