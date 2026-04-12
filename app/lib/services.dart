import 'dart:async';
import 'dart:io';
import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'package:flutter/services.dart' show rootBundle;
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa_onnx;
import 'config.dart';


class ApiService {
  Future<String?> uploadAudio(List<int> pcmBuffer) async {
    try {
      var request = http.MultipartRequest('POST', Uri.parse('${AppConfig.baseUrl}/upload'));
      request.files.add(http.MultipartFile.fromBytes('audio_file', pcmBuffer, filename: 'command.pcm'));

      var response = await request.send();
      if (response.statusCode == 200) {
        var responseBody = await response.stream.bytesToString();
        var data = jsonDecode(responseBody);
        return data['task_id'];
      }
      return null;
    } catch (e) {
      return null;
    }
  }

  Stream<String> listenToTextStream(String taskId) async* {
    final client = http.Client();
    final request = http.Request('GET', Uri.parse('${AppConfig.baseUrl}/text_stream/$taskId'));
    
    // Set standard SSE headers
    request.headers['Accept'] = 'text/event-stream';
    request.headers['Cache-Control'] = 'no-cache';

    try {
      final response = await client.send(request);
      if (response.statusCode != 200) {
        throw Exception('Failed to connect to text stream: ${response.statusCode}');
      }

      // Transform raw bytes to string lines
      final lineStream = response.stream
          .transform(utf8.decoder)
          .transform(const LineSplitter());

      await for (final line in lineStream) {
        if (line.startsWith('data: ')) {
          final content = line.substring(6).trim();
          
          if (content == '[DONE]') {
            break; // Stop the stream when server sends [DONE]
          }

          try {
            final data = jsonDecode(content);
            final token = data['token'] as String;
            yield token; // Emit the token to the listener
          } catch (e) {
            // Ignore malformed JSON or empty heartbeats
          }
        }
      }
    } finally {
      client.close(); // Ensure client is closed when stream ends
    }
  }
}

class AudioService {
  final AudioRecorder _recorder = AudioRecorder();
  final AudioPlayer _player = AudioPlayer();

  AudioService() {
    _player.setAudioContext(AudioContext(
      android: const AudioContextAndroid(
        audioFocus: AndroidAudioFocus.none,
        contentType: AndroidContentType.music, 
        usageType: AndroidUsageType.media,
      ),
      iOS: AudioContextIOS(
        category: AVAudioSessionCategory.playAndRecord,
        options: {
          AVAudioSessionOptions.defaultToSpeaker,
          AVAudioSessionOptions.mixWithOthers,
          AVAudioSessionOptions.allowBluetooth,
          AVAudioSessionOptions.allowBluetoothA2DP, 
        },
      ),
    ));
  }

  // get the event stream of the player completion
  Stream<void> get onPlayerComplete => _player.onPlayerComplete;

  Future<Stream<List<int>>> startMicStream() async {
    return await _recorder.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits, 
        sampleRate: AppConfig.sampleRate, 
        numChannels: 1));
  }

  Future<void> playLocalFile(String path) async {
    await _player.play(DeviceFileSource(path));
  }

  Future<void> stopPlayer() async {
    await _player.stop();
    await _player.release();
  }

  Future<void> playStreamUrl(String url) async {
    await _player.play(UrlSource(url));
  }

  void dispose() {
    _recorder.dispose();
    _player.dispose();
  }
}

class KwsService {
  sherpa_onnx.KeywordSpotter? _kws;
  sherpa_onnx.OnlineStream? _onlineStream;

  Future<void> initKWS() async {
    sherpa_onnx.initBindings();
    final encoderPath = await _copyAsset('assets/kws_model/encoder-epoch-13-avg-2-chunk-16-left-64.onnx');
    final decoderPath = await _copyAsset('assets/kws_model/decoder-epoch-13-avg-2-chunk-16-left-64.onnx');
    final joinerPath = await _copyAsset('assets/kws_model/joiner-epoch-13-avg-2-chunk-16-left-64.onnx');
    final tokensPath = await _copyAsset('assets/kws_model/tokens.txt');
    final keywordsPath = await _copyAsset('assets/kws_model/keywords.txt');

    final transducer = sherpa_onnx.OnlineTransducerModelConfig(
        encoder: encoderPath, decoder: decoderPath, joiner: joinerPath);
    final modelConfig = sherpa_onnx.OnlineModelConfig(
        transducer: transducer, tokens: tokensPath, debug: true);

    final config = sherpa_onnx.KeywordSpotterConfig(
      model: modelConfig,
      keywordsFile: keywordsPath,
      keywordsThreshold: 0.1,
      keywordsScore: 1.5,
      feat: const sherpa_onnx.FeatureConfig(sampleRate: AppConfig.sampleRate, featureDim: 80),
    );

    _kws = sherpa_onnx.KeywordSpotter(config);
    _onlineStream = _kws!.createStream();
  }

  // detect the wake word, return true if detected
  bool detectWakeWord(Float32List float32List) {
    if (_kws == null || _onlineStream == null) return false;
    
    _onlineStream!.acceptWaveform(samples: float32List, sampleRate: AppConfig.sampleRate);
    while (_kws!.isReady(_onlineStream!)) {
      _kws!.decode(_onlineStream!);
      final keyword = _kws!.getResult(_onlineStream!).keyword;
      if (keyword.isNotEmpty) return true;
    }
    return false;
  }

  void resetStream() {
    if (_kws != null && _onlineStream != null) {
      _kws!.reset(_onlineStream!);
    }
  }

  void hardResetStream() {
    if (_kws != null) {
      _onlineStream?.free();
      _onlineStream = _kws!.createStream();
    }
  }

  Future<String> _copyAsset(String assetPath) async {
    final directory = await getApplicationDocumentsDirectory();
    final file = File('${directory.path}/$assetPath');
    await file.parent.create(recursive: true);
    final data = await rootBundle.load(assetPath);
    await file.writeAsBytes(data.buffer.asUint8List(), flush: true);
    return file.path;
  }

  void dispose() {
    _onlineStream?.free();
    _kws?.free();
  }
}