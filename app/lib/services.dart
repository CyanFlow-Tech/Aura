import 'dart:async';
import 'dart:io';
import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:flutter/services.dart' show rootBundle;
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa_onnx;
import 'config.dart';

void _slog(String tag, String msg) {
  final ts = DateTime.now().toIso8601String().substring(11);
  debugPrint('[$ts][SVC/$tag] $msg');
}


class ApiService {
  /// Upload the recorded PCM. Pass [sessionId] = null on the FIRST turn of a
  /// new conversation; the cloud will allocate a new session and return its
  /// id. On every subsequent turn pass the cached id back so the cloud keeps
  /// the same dialogue context. Returns the (possibly new) session_id, or
  /// null on failure.
  Future<String?> uploadAudio(
    List<int> pcmBuffer, {
    String? sessionId,
  }) async {
    final t0 = DateTime.now();
    final url = '${AppConfig.baseUrl}/upload';
    _slog('UPLOAD',
        'POST $url  bytes=${pcmBuffer.length}  sentSid=$sessionId');
    try {
      var request = http.MultipartRequest('POST', Uri.parse(url));
      request.headers['X-Aura-Token'] = AppConfig.apiKey;

      request.files.add(http.MultipartFile.fromBytes('audio_file', pcmBuffer, filename: 'command.pcm'));
      if (sessionId != null) {
        request.fields['session_id'] = sessionId;
      }

      var response = await request.send();
      final dt = DateTime.now().difference(t0).inMilliseconds;
      if (response.statusCode == 200) {
        var responseBody = await response.stream.bytesToString();
        _slog('UPLOAD', '200 in ${dt}ms  body=$responseBody');
        var data = jsonDecode(responseBody) as Map<String, dynamic>;
        // Cloud always echoes the session_id back: a freshly minted one for
        // new conversations, the same id we sent for continuing ones. Fall
        // back to the id we sent if the server omits it, so the conversation
        // doesn't break.
        final returnedSessionId = (data['session_id'] as String?) ?? sessionId;
        if (returnedSessionId == null) {
          _slog('UPLOAD', 'response missing session_id: $responseBody');
          return null;
        }
        return returnedSessionId;
      } else {
        final body = await response.stream.bytesToString();
        _slog('UPLOAD', 'FAIL status=${response.statusCode} in ${dt}ms body=$body');
      }
      return null;
    } catch (e, st) {
      _slog('UPLOAD', 'EXC after ${DateTime.now().difference(t0).inMilliseconds}ms: $e');
      _slog('UPLOAD', 'stack: $st');
      return null;
    }
  }

  // notify the cloud to abort the current model output for this session
  Future<bool> interrupt(String sessionId) async {
    final t0 = DateTime.now();
    final url = '${AppConfig.baseUrl}/interrupt/$sessionId';
    _slog('INT', 'POST $url');
    try {
      final resp = await http.post(
        Uri.parse(url),
        headers: {'X-Aura-Token': AppConfig.apiKey},
      );
      _slog('INT',
          'status=${resp.statusCode} in ${DateTime.now().difference(t0).inMilliseconds}ms body=${resp.body}');
      return resp.statusCode == 200;
    } catch (e) {
      _slog('INT', 'EXC: $e');
      return false;
    }
  }

  // notify the cloud that the multi-turn session is complete
  Future<bool> sessionComplete({String? sessionId}) async {
    try {
      final uri = Uri.parse('${AppConfig.baseUrl}/session_complete'
          '${sessionId != null ? '?session_id=$sessionId' : ''}');
      final resp = await http.post(
        uri,
        headers: {'X-Aura-Token': AppConfig.apiKey},
      );
      return resp.statusCode == 200;
    } catch (e) {
      debugPrint('Error completing session: ${e.toString()}');
      return false;
    }
  }

  Stream<String> listenToTextStream(String sessionId) async* {
    final client = http.Client();
    final url = '${AppConfig.baseUrl}/text_stream/$sessionId';
    final request = http.Request('GET', Uri.parse(url));

    // Set standard SSE headers
    request.headers['Accept'] = 'text/event-stream';
    request.headers['Cache-Control'] = 'no-cache';
    request.headers['X-Aura-Token'] = AppConfig.apiKey;

    final t0 = DateTime.now();
    int elapsed() => DateTime.now().difference(t0).inMilliseconds;
    _slog('TXT', 'GET $url');

    try {
      final response = await client.send(request);
      _slog('TXT',
          'response status=${response.statusCode}  ct=${response.headers['content-type']}  @${elapsed()}ms');
      if (response.statusCode != 200) {
        throw Exception('Failed to connect to text stream: ${response.statusCode}');
      }

      // Transform raw bytes to string lines
      final lineStream = response.stream
          .transform(utf8.decoder)
          .transform(const LineSplitter());

      int lineNo = 0;
      await for (final line in lineStream) {
        lineNo++;
        if (lineNo <= 3 || lineNo % 50 == 0) {
          final preview = line.length > 80 ? '${line.substring(0, 80)}...' : line;
          _slog('TXT', 'line #$lineNo @${elapsed()}ms: $preview');
        }
        if (line.startsWith('data: ')) {
          final content = line.substring(6).trim();

          if (content == '[DONE]') {
            _slog('TXT', '[DONE] @${elapsed()}ms (after $lineNo lines)');
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
      _slog('TXT', 'stream loop EXITED @${elapsed()}ms (lines=$lineNo)');
    } catch (e, st) {
      _slog('TXT', 'EXC @${elapsed()}ms: $e');
      _slog('TXT', 'stack: $st');
      rethrow;
    } finally {
      client.close(); // Ensure client is closed when stream ends
      _slog('TXT', 'client closed @${elapsed()}ms');
    }
  }
}

class AudioService {
  final AudioRecorder _recorder = AudioRecorder();

  // Two physically separate players so the controller can distinguish between
  // "short prompt sound finished" and "cloud audio_stream finished".
  // Sharing one player makes onPlayerComplete events ambiguous and races with
  // the FSM transition out of state3 (the prompt's complete event can leak
  // into state3's listener and prematurely advance to state5).
  final AudioPlayer _promptPlayer = AudioPlayer(); // local asset wavs
  final AudioPlayer _streamPlayer = AudioPlayer(); // cloud TTS stream

  AudioService() {
    final ctx = AudioContext(
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
    );
    _promptPlayer.setAudioContext(ctx);
    _streamPlayer.setAudioContext(ctx);

    // Echo every player state change for the cloud TTS player. This is the
    // single most useful signal for diagnosing "audio_stream silent" bugs:
    // we will see whether MediaPlayer reaches `playing`, stays in `paused`
    // (buffering), or never leaves `stopped`.
    _streamPlayer.onPlayerStateChanged.listen((s) {
      _slog('AUD', 'streamPlayer state → $s');
    });
    _streamPlayer.onPlayerComplete.listen((_) {
      _slog('AUD', 'streamPlayer COMPLETE');
    });
    _streamPlayer.onLog.listen((msg) {
      _slog('AUD', 'streamPlayer log: $msg');
    });
  }

  // Expose current state of the stream player so the controller can include
  // it in transition logs.
  PlayerState get streamPlayerState => _streamPlayer.state;

  // ONLY the cloud stream player's natural completion. The controller relies
  // on this to leave state3 → state5; do NOT mix prompt completion in here.
  Stream<void> get onStreamComplete => _streamPlayer.onPlayerComplete;

  // True while the cloud stream player still has audio to render.
  // audioplayers' onPlayerComplete fires unreliably for streamed UrlSource
  // (it triggers when the HTTP body is exhausted but buffered audio is still
  // playing), so the controller polls this for the real end-of-playback.
  bool get isStreamPlayerActive {
    final s = _streamPlayer.state;
    return s == PlayerState.playing || s == PlayerState.paused;
  }

  Future<Stream<List<int>>> startMicStream() async {
    return await _recorder.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: AppConfig.sampleRate,
        numChannels: 1));
  }

  Future<void> playLocalFile(String path) async {
    await _promptPlayer.play(DeviceFileSource(path));
  }

  Future<void> stopPromptPlayer() async {
    await _promptPlayer.stop();
  }

  Future<void> stopStreamPlayer() async {
    await _streamPlayer.stop();
    await _streamPlayer.release();
  }

  Future<void> stopAllPlayers() async {
    await _promptPlayer.stop();
    await _streamPlayer.stop();
    await _streamPlayer.release();
  }

  Future<void> playStreamUrl(String url) async {
    // Intentionally do NOT touch _promptPlayer here. The controller wants
    // any in-flight prompt (e.g. i_am_thinking.wav) to play through to its
    // natural end concurrently with the cloud audio. The two players are
    // physically independent and the AudioContext has mixWithOthers set, so
    // brief overlap is fine.
    final t0 = DateTime.now();
    _slog('AUD', 'streamPlayer.play(UrlSource) START url=$url');
    // Hard-tear the underlying MediaPlayer before kicking off a new prepare.
    // Without this, if a previous play()'s prepareAsync is still in flight
    // (common when the cloud was slow on the previous turn — we've seen
    // resolves come in 22s late), the new play() does NOT cancel it, and we
    // end up with two concurrent native MediaPlayers feeding the same Dart
    // wrapper. The result is stale TTS from an interrupted turn surfacing
    // on top of the current turn. stop()+release() forces audioplayers to
    // throw away the old MediaPlayer instance.
    try {
      await _streamPlayer.stop();
      await _streamPlayer.release();
    } catch (e) {
      _slog('AUD', 'streamPlayer pre-play stop/release ignored: $e');
    }
    try {
      await _streamPlayer.play(UrlSource(url));
      _slog('AUD',
          'streamPlayer.play() RESOLVED in ${DateTime.now().difference(t0).inMilliseconds}ms  state=${_streamPlayer.state}');
    } catch (e, st) {
      _slog('AUD',
          'streamPlayer.play() THREW in ${DateTime.now().difference(t0).inMilliseconds}ms: $e');
      _slog('AUD', 'stack: $st');
      rethrow;
    }
  }

  // Start a bundled asset wav and resolve once playback has started.
  // Use this for "fire-and-forget" prompts that should already be audible
  // when the caller proceeds (e.g. i_am_thinking.wav before the upload RTT).
  Future<void> playAsset(String assetPath) async {
    await _promptPlayer.stop();
    await _promptPlayer.play(AssetSource(assetPath));
  }

  // play a bundled asset wav on the prompt player and resolve on completion
  Future<void> playAssetAndWait(String assetPath) async {
    await _promptPlayer.stop();
    final completer = Completer<void>();
    late StreamSubscription<void> sub;
    sub = _promptPlayer.onPlayerComplete.listen((_) {
      sub.cancel();
      if (!completer.isCompleted) completer.complete();
    });
    try {
      await _promptPlayer.play(AssetSource(assetPath));
      await completer.future.timeout(const Duration(seconds: 30), onTimeout: () {
        sub.cancel();
      });
    } catch (e) {
      sub.cancel();
      rethrow;
    }
  }

  void dispose() {
    _recorder.dispose();
    _promptPlayer.dispose();
    _streamPlayer.dispose();
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

  // feed the audio frame and return the matched keyword (empty if none)
  // NOTE: keywords.txt must contain all keywords used by the controller, e.g.
  //   小爱同学 / 请等一下 / 我明白了
  // Generate the pinyin token line for each keyword via sherpa-onnx text2token tool.
  String detectKeyword(Float32List float32List) {
    if (_kws == null || _onlineStream == null) return '';

    _onlineStream!.acceptWaveform(samples: float32List, sampleRate: AppConfig.sampleRate);
    while (_kws!.isReady(_onlineStream!)) {
      _kws!.decode(_onlineStream!);
      final keyword = _kws!.getResult(_onlineStream!).keyword;
      if (keyword.isNotEmpty) return keyword;
    }
    return '';
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