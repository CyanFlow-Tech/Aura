import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';
import 'config.dart';
import 'services.dart';

enum AppMode { kws, recording, processing }

class AuraController extends ChangeNotifier {
  // inject the services
  final ApiService _apiService = ApiService();
  final AudioService _audioService = AudioService();
  final KwsService _kwsService = KwsService();
  
  StreamSubscription<List<int>>? _micSubscription;
  StreamSubscription<String>? _textStreamSubscription;
  String _accumulatedLlmReply = '';

  // state variables
  AppMode currentMode = AppMode.kws;
  String displayText = '正在初始化 Aura 听觉神经...\n首次启动需要释放模型文件，请稍候';
  
  List<int> _pcmBuffer = [];
  DateTime? _lastLoudTime;
  bool _isDisposed = false;

  AuraController() {
    _initSystem();
  }

  void _updateState(AppMode mode, String text) {
    if (_isDisposed) return;
    currentMode = mode;
    displayText = text;
    notifyListeners();
  }

  Future<void> _initSystem() async {
    // listen to the player completion event
    _audioService.onPlayerComplete.listen((_) {
      _fastResetToKws();
    });

    final status = await Permission.microphone.request();
    if (status != PermissionStatus.granted) {
      _updateState(AppMode.kws, '需要麦克风权限才能唤醒！');
      return;
    }
    
    await _kwsService.initKWS();
    final stream = await _audioService.startMicStream();
      
    _updateState(AppMode.kws, 'Aura 已就绪\n请试着喊："小爱同学"');
    _micSubscription = stream.listen(_processAudioStream);
  }

  // process the microphone incoming data
  void _processAudioStream(List<int> data) {
    final pcmBytes = Uint8List.fromList(data);
    final byteData = ByteData.sublistView(pcmBytes);
    final sampleCount = pcmBytes.lengthInBytes ~/ 2;
    final float32List = Float32List(sampleCount);
    
    double volumeSum = 0;
    for (int i = 0; i < sampleCount; i++) {
      final sample = byteData.getInt16(i * 2, Endian.little);
      float32List[i] = sample / 32768.0;
      volumeSum += float32List[i].abs();
    }
    double currentVolume = volumeSum / float32List.length;

    // state machine branches
    if (currentMode == AppMode.kws) {
      bool isWakeup = _kwsService.detectWakeWord(float32List);
      if (isWakeup) {
        _kwsService.resetStream();
        _startRecording();
      }
    } else if (currentMode == AppMode.recording) {
      final safeLength = data.length % 2 == 0 ? data.length : data.length - 1;
      _pcmBuffer.addAll(data.sublist(0, safeLength));
      
      if (currentVolume > AppConfig.volumeThreshold) {
        _lastLoudTime = DateTime.now();
      }
      
      final quietDuration = DateTime.now().difference(_lastLoudTime!).inMilliseconds;
      final recordSeconds = _pcmBuffer.length / (AppConfig.sampleRate * 2);
      
      if (quietDuration > AppConfig.quietDurationMs || recordSeconds > AppConfig.maxRecordSeconds) {
        _stopAndProcessAudio();
      }
    }
  }

  void _startRecording() {
    _pcmBuffer.clear();
    _lastLoudTime = DateTime.now();
    _updateState(AppMode.recording, '我在听...\n(请说出你的问题)');
  }

  Future<void> _stopAndProcessAudio() async {
    if (currentMode != AppMode.recording) return;
    _updateState(AppMode.processing, '正在思考中...');
    
    // 1. Upload audio and get taskId
    String? taskId = await _apiService.uploadAudio(_pcmBuffer);
    if (taskId == null) {
      _errorResetToKws('大脑短路了：上传失败');
      return;
    }

    // 2. Prepare for new response
    _accumulatedLlmReply = ''; // Reset the reply buffer
    await _audioService.stopPlayer();

    // 3. Start SSE Text Stream (Parallel with Audio)
    _startSseTextStream(taskId);

    // 4. Start Audio Stream
    String streamUrl = '${AppConfig.baseUrl}/stream/$taskId.mp3';
    try {
      await _audioService.playStreamUrl(streamUrl);
    } catch (e) {
      _errorResetToKws('流式连接失败，请检查网络');
    }
  }

  void _startSseTextStream(String taskId) {
    // Cancel any existing subscription before starting a new one
    _textStreamSubscription?.cancel();

    _textStreamSubscription = _apiService
        .listenToTextStream(taskId)
        .listen((token) {
          _accumulatedLlmReply += token;
          // Update the UI with both user text and the streaming reply
          // Note: You may need to store user_text in a variable after upload
          _updateState(AppMode.processing, 'Aura: $_accumulatedLlmReply');
        }, onError: (error) {
          debugPrint('Text Stream Error: $error');
        }, onDone: () {
          debugPrint('Text Stream Completed');
        });
  }

  // 🟢 极速重置：用于正常对话结束，0延迟瞬间恢复听力！
  void _fastResetToKws() {
    if (_isDisposed) return;
    
    _textStreamSubscription?.cancel();
    _textStreamSubscription = null;
    _accumulatedLlmReply = '';
    _pcmBuffer.clear();
    
    _updateState(AppMode.kws, 'Aura 就绪\n请试着喊："小爱同学"');
    _kwsService.hardResetStream(); // 瞬间重置唤醒引擎
  }

  // 🔴 错误重置：用于网络崩溃等异常，保留2秒让用户看清错误提示
  void _errorResetToKws(String errorMsg) {
    _textStreamSubscription?.cancel();
    _textStreamSubscription = null;
    
    // 第一时间把错误信息打到屏幕上
    _updateState(AppMode.kws, errorMsg); 
    
    // 强制等待2秒，不让底层录音机抢夺焦点
    Future.delayed(const Duration(seconds: 2), () {
      if (_isDisposed) return;
      
      _accumulatedLlmReply = '';
      _pcmBuffer.clear();
      _updateState(AppMode.kws, 'Aura 就绪\n请试着喊："小爱同学"');
      _kwsService.hardResetStream();
    });
  }

  @override
  void dispose() {
    _isDisposed = true;
    _micSubscription?.cancel();
    _audioService.dispose();
    _kwsService.dispose();
    _textStreamSubscription?.cancel();
    super.dispose();
  }
}