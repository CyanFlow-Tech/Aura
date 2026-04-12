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
  String? _currentPlayingFilePath;

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
      if (_currentPlayingFilePath != null) {
        _audioService.cleanupLocalFile(_currentPlayingFilePath!);
      }
      Future.delayed(const Duration(milliseconds: 500), _resetToKws);
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
    _updateState(AppMode.processing, '录音结束，正在发送给大脑...');

    // 1. upload audio
    String? taskId = await _apiService.uploadAudio(_pcmBuffer);
    if (taskId == null) {
      _updateState(AppMode.kws, '大脑短路了：上传失败');
      _hardResetToKws();
      return;
    }

    _updateState(AppMode.processing, '正在思考中...');
    await _audioService.stopPlayer();

    // 2. streaming download
    _currentPlayingFilePath = await _apiService.downloadStreamAndSave(taskId);
    
    // 3. play audio
    if (_currentPlayingFilePath != null) {
      await _audioService.playLocalFile(_currentPlayingFilePath!);
    } else {
      _updateState(AppMode.kws, '后端生成失败或网络中断');
      _hardResetToKws();
    }
  }

  void _resetToKws() {
    if (_isDisposed) return;
    _updateState(AppMode.kws, 'Aura 再次就绪\n请试着喊："小爱同学"');
    _pcmBuffer.clear();
    _kwsService.resetStream();
  }

  void _hardResetToKws() {
    Future.delayed(const Duration(seconds: 2), () {
      if (_isDisposed) return;
      _updateState(AppMode.kws, 'Aura 再次就绪\n请试着喊："小爱同学"');
      _pcmBuffer.clear();
      _kwsService.hardResetStream();
    });
  }

  @override
  void dispose() {
    _isDisposed = true;
    _micSubscription?.cancel();
    _audioService.dispose();
    _kwsService.dispose();
    super.dispose();
  }
}