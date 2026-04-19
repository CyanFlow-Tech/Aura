import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';
import 'config.dart';
import 'services.dart';

// Lightweight tagged debug log with monotonic ms timestamp so the gateway and
// Flutter side can be aligned. Strip these by removing the calls (they are
// `debugPrint` so they vanish in release builds anyway).
void _dlog(String tag, String msg) {
  final ts = DateTime.now().toIso8601String().substring(11);
  debugPrint('[$ts][CTRL/$tag] $msg');
}

/// 7-state Aura conversation FSM
///
/// state0_kws         : idle, waiting for the wake word "小爱同学"
/// state1_listening   : play i_am_listening.wav, then VAD-record the question
/// state2_uploading   : play i_am_thinking.wav, upload PCM, wait for cloud reply
/// state3_responding  : cloud is streaming back; also listen for "请等一下"
/// state4_interrupting: send interrupt signal, then return to state1
/// state5_followup    : play am_i_clear.wav, listen for "我明白了" or new question
/// state6_sessionEnd  : send session complete, clear local state, back to state0
enum AppMode {
  state0Kws,
  state1Listening,
  state2Uploading,
  state3Responding,
  state4Interrupting,
  state5Followup,
  state6SessionEnd,
}

class AuraController extends ChangeNotifier {
  final ApiService _apiService = ApiService();
  final AudioService _audioService = AudioService();
  final KwsService _kwsService = KwsService();

  StreamSubscription<List<int>>? _micSubscription;
  StreamSubscription<String>? _textStreamSubscription;
  // bumped on every state transition; used to ignore stale async callbacks
  int _stateGeneration = 0;

  // public state
  AppMode currentMode = AppMode.state0Kws;
  String displayText = '正在初始化 Aura 听觉神经...\n首次启动需要释放模型文件，请稍候';

  // session bookkeeping
  String _accumulatedLlmReply = '';
  // Multi-turn conversation id. Allocated by the cloud on the FIRST turn
  // (when we upload with session_id == null) and reused on every subsequent
  // turn so the cloud keeps the same dialogue context. Also identifies the
  // text/audio streams and the interrupt target. Cleared only when the
  // whole session ends (state6) so the next state0→state1→state2 cycle
  // correctly starts a brand-new conversation.
  String? _currentSessionId;
  bool _cloudStreamActive = false;     // text stream still flowing
  bool _audioPlaybackActive = false;   // audio_stream player still emitting sound

  // Monotonic id of the current audio_stream play() attempt. Bumped every
  // time we kick off a new play, and every time we abandon the current turn
  // (state4 interrupt). The value captured locally before `play()` is then
  // compared against this field once the Future finally resolves: a mismatch
  // means we are looking at the late completion of a play that belongs to a
  // turn the user has already moved past, and we must NOT let it start
  // emitting sound — otherwise audio from an interrupted previous answer can
  // surface in the middle of the next turn.
  int _audioStreamSeq = 0;

  // recording bookkeeping
  final List<int> _pcmBuffer = [];
  DateTime? _lastLoudTime;
  bool _vadArmed = false;          // start VAD only after first wav prompt is done

  // followup (state 5) bookkeeping
  Timer? _followupTimer;
  bool _followupListening = false; // armed after am_i_clear.wav playback ends

  bool _isDisposed = false;

  AuraController() {
    _initSystem();
  }

  // ────────────────────────────────────────────────────────────────────────────
  // bootstrap
  // ────────────────────────────────────────────────────────────────────────────

  Future<void> _initSystem() async {
    final status = await Permission.microphone.request();
    if (status != PermissionStatus.granted) {
      _setState(AppMode.state0Kws, '需要麦克风权限才能唤醒！');
      return;
    }

    await _kwsService.initKWS();
    final stream = await _audioService.startMicStream();

    _enterState0Kws(initial: true);
    _micSubscription = stream.listen(_onMicFrame);
  }

  void _setState(AppMode mode, String text) {
    if (_isDisposed) return;
    if (currentMode != mode) {
      _dlog('STATE', '${currentMode.name} → ${mode.name}  (gen ${_stateGeneration + 1})  sid=$_currentSessionId');
      _stateGeneration++;
    }
    currentMode = mode;
    displayText = text;
    notifyListeners();
  }

  // ────────────────────────────────────────────────────────────────────────────
  // mic dispatcher: routes every PCM frame to the active state's handler
  // ────────────────────────────────────────────────────────────────────────────

  void _onMicFrame(List<int> data) {
    if (_isDisposed) return;

    final pcmBytes = Uint8List.fromList(data);
    final byteData = ByteData.sublistView(pcmBytes);
    final sampleCount = pcmBytes.lengthInBytes ~/ 2;
    final float32List = Float32List(sampleCount);
    double volumeSum = 0;
    for (int i = 0; i < sampleCount; i++) {
      final s = byteData.getInt16(i * 2, Endian.little);
      float32List[i] = s / 32768.0;
      volumeSum += float32List[i].abs();
    }
    final currentVolume =
        sampleCount == 0 ? 0.0 : volumeSum / sampleCount;

    switch (currentMode) {
      case AppMode.state0Kws:
        _handleState0(float32List);
        break;
      case AppMode.state1Listening:
        if (_vadArmed) _handleState1Vad(data, currentVolume);
        break;
      // state2 (uploading + cloud thinking) is by far the longest window in
      // a turn — feed the same interrupt KWS handler as state3 so the user
      // can say "请等一下" before the cloud has even started replying.
      case AppMode.state2Uploading:
      case AppMode.state3Responding:
        _handleInterruptKws(float32List);
        break;
      case AppMode.state5Followup:
        _handleState5(data, float32List, currentVolume);
        break;
      case AppMode.state4Interrupting:
      case AppMode.state6SessionEnd:
        // these states ignore mic input
        break;
    }
  }

  // ────────────────────────────────────────────────────────────────────────────
  // state 0 — listen for wake word "小爱同学"
  // ────────────────────────────────────────────────────────────────────────────

  void _enterState0Kws({bool initial = false}) {
    _cancelFollowupTimer();
    _followupListening = false;
    _vadArmed = false;
    _pcmBuffer.clear();
    _accumulatedLlmReply = '';
    // state0 is only (re)entered after a session has fully wrapped up
    // (state6), or on a hard error reset. In both cases the next turn
    // starts a fresh dialogue, so drop the cached session id.
    _currentSessionId = null;
    _cloudStreamActive = false;
    _audioPlaybackActive = false;
    _kwsService.hardResetStream();
    _setState(
      AppMode.state0Kws,
      initial ? 'Aura 已就绪\n请试着喊："小爱同学"' : 'Aura 就绪\n请试着喊："小爱同学"',
    );
  }

  void _handleState0(Float32List frame) {
    final keyword = _kwsService.detectKeyword(frame);
    if (keyword.isEmpty) return;
    if (keyword.contains(AppConfig.wakeKeyword)) {
      _kwsService.resetStream();
      _enterState1Listening();
    }
  }

  // ────────────────────────────────────────────────────────────────────────────
  // state 1 — play i_am_listening.wav, then VAD-record user question
  // ────────────────────────────────────────────────────────────────────────────

  Future<void> _enterState1Listening() async {
    _vadArmed = false;
    _pcmBuffer.clear();
    _setState(AppMode.state1Listening, '我在听...');

    try {
      await _audioService.playAssetAndWait('wav/i_am_listening.wav');
    } catch (e) {
      debugPrint('Failed to play i_am_listening.wav: $e');
    }
    if (_isDisposed || currentMode != AppMode.state1Listening) return;

    _lastLoudTime = DateTime.now();
    _vadArmed = true;
    _setState(AppMode.state1Listening, '我在听...\n(请说出你的问题)');
  }

  void _handleState1Vad(List<int> data, double currentVolume) {
    final safeLength = data.length % 2 == 0 ? data.length : data.length - 1;
    _pcmBuffer.addAll(data.sublist(0, safeLength));

    if (currentVolume > AppConfig.volumeThreshold) {
      _lastLoudTime = DateTime.now();
    }

    final quietMs = DateTime.now().difference(_lastLoudTime!).inMilliseconds;
    final recordSec = _pcmBuffer.length / (AppConfig.sampleRate * 2);

    if (quietMs > AppConfig.quietDurationMs ||
        recordSec > AppConfig.maxRecordSeconds) {
      _vadArmed = false;
      _enterState2Uploading();
    }
  }

  // ────────────────────────────────────────────────────────────────────────────
  // state 2 — play i_am_thinking.wav, upload audio, wait for first stream chunk
  // ────────────────────────────────────────────────────────────────────────────

  Future<void> _enterState2Uploading() async {
    final t0 = DateTime.now();
    int elapsed() => DateTime.now().difference(t0).inMilliseconds;

    _setState(AppMode.state2Uploading, '正在思考中...');

    // Reset KWS context ONCE here. The same context is reused across state2
    // (upload + cloud thinking) AND state3 (cloud streaming) so an interrupt
    // word that straddles the state2→state3 boundary is not chopped in half.
    _kwsService.hardResetStream();

    final pcmBytes = _pcmBuffer.length;
    _dlog('STATE2',
        'enter  pcmBytes=$pcmBytes (~${(pcmBytes / (AppConfig.sampleRate * 2)).toStringAsFixed(2)}s) cachedSid=$_currentSessionId');

    // Play the thinking prompt SEQUENTIALLY: await full playback completion
    // before starting the upload. Parallel/fire-and-forget plays were proving
    // unreliable on Android — audioplayers' play() Future resolves while the
    // MediaPlayer is still in prepareAsync, and any subsequent state-machine
    // operation (stop, focus change, GC, new source) can drop the prompt
    // before the speaker has ever emitted audio. The 1s prompt is short
    // compared to typical end-to-end response time, so this is acceptable.
    try {
      await _audioService.playAssetAndWait('wav/i_am_thinking.wav');
    } catch (e) {
      debugPrint('Failed to play i_am_thinking.wav: $e');
    }
    if (_isDisposed || currentMode != AppMode.state2Uploading) return;
    _dlog('STATE2', 'thinking.wav done @${elapsed()}ms — about to /upload');

    // Carry the cached session_id forward. On the first turn it is null,
    // and the cloud will mint a new one and echo it back; on follow-up
    // turns we send the same id so the cloud keeps the dialogue context.
    final returnedSessionId = await _apiService.uploadAudio(
      _pcmBuffer,
      sessionId: _currentSessionId,
    );
    _pcmBuffer.clear();
    if (_isDisposed) return;
    _dlog('STATE2',
        '/upload returned sid=$returnedSessionId  @${elapsed()}ms  mode=${currentMode.name}');
    // The user may have said "请等一下" during the upload await — in which
    // case state2 has already exited (most likely via state4 → state1). If
    // we don't bail here we'll stomp the new state by spinning up the text
    // and audio streams for a turn the user already chose to abandon.
    if (currentMode != AppMode.state2Uploading) {
      _dlog('STATE2', 'mode changed during upload — aborting this turn');
      // The cloud already started this turn; ask it to abort. Adopt the
      // (possibly new) session_id anyway so the next turn within the same
      // conversation continues to glue context together.
      if (returnedSessionId != null) {
        _currentSessionId = returnedSessionId;
        unawaited(_apiService.interrupt(returnedSessionId));
      }
      return;
    }
    if (returnedSessionId == null) {
      _errorResetToKws('大脑短路了：上传失败');
      return;
    }
    _currentSessionId = returnedSessionId;
    _accumulatedLlmReply = '';

    final sessionId = returnedSessionId;

    // we transition to state 3 the moment ANY stream channel produces data
    bool transitioned = false;
    void goState3(String trigger) {
      if (transitioned) return;
      transitioned = true;
      _dlog('STATE2', 'state3 trigger=$trigger @${elapsed()}ms');
      if (currentMode != AppMode.state2Uploading) {
        _dlog('STATE2', 'goState3 dropped: mode is ${currentMode.name}');
        return;
      }
      _enterState3Responding();
    }

    _cloudStreamActive = true;
    _audioPlaybackActive = false;

    // text stream
    _textStreamSubscription?.cancel();
    _dlog('STREAM', 'text_stream subscribing  sid=$sessionId  @${elapsed()}ms');
    int textTokens = 0;
    int textBytes = 0;
    final tStream = DateTime.now();
    _textStreamSubscription = _apiService.listenToTextStream(sessionId).listen(
      (token) {
        textTokens++;
        textBytes += token.length;
        if (textTokens <= 3 || textTokens % 20 == 0) {
          _dlog('STREAM',
              'text token #$textTokens @${DateTime.now().difference(tStream).inMilliseconds}ms len=${token.length} preview=${token.length > 12 ? '${token.substring(0, 12)}...' : token}');
        }
        _accumulatedLlmReply += token;
        if (currentMode == AppMode.state2Uploading ||
            currentMode == AppMode.state3Responding) {
          _setState(currentMode, 'Aura: $_accumulatedLlmReply');
        }
        goState3('text');
      },
      onError: (e, st) {
        _dlog('STREAM', 'text_stream ERROR after $textTokens tokens / ${DateTime.now().difference(tStream).inMilliseconds}ms: $e');
      },
      onDone: () {
        _dlog('STREAM',
            'text_stream DONE  tokens=$textTokens bytes=$textBytes durMs=${DateTime.now().difference(tStream).inMilliseconds}');
        _onCloudStreamFinished();
      },
    );

    // audio stream — first byte from the player is also a valid trigger
    final encodedToken = Uri.encodeComponent(AppConfig.apiKey);
    final streamUrl =
        '${AppConfig.baseUrl}/audio_stream/$sessionId.mp3?token=$encodedToken';
    final myAudioSeq = ++_audioStreamSeq;
    _dlog('STREAM',
        'audio_stream play()  seq=$myAudioSeq  url=$streamUrl  @${elapsed()}ms');
    final tAudio = DateTime.now();
    try {
      await _audioService.playStreamUrl(streamUrl);
      if (_isDisposed) return;
      // If a newer turn (or an interrupt) has bumped the sequence while we
      // were waiting for prepareAsync, this Future is the late completion
      // of an abandoned play. Native MediaPlayer just transitioned to
      // `playing` and would otherwise start blasting stale TTS audio over
      // whatever the user is doing now — so stop it immediately.
      if (myAudioSeq != _audioStreamSeq) {
        _dlog('STREAM',
            'audio resolved STALE  seq=$myAudioSeq cur=$_audioStreamSeq  @${DateTime.now().difference(tAudio).inMilliseconds}ms — stopping');
        await _audioService.stopStreamPlayer();
        return;
      }
      _dlog('STREAM',
          'audio_stream play() RESOLVED @${DateTime.now().difference(tAudio).inMilliseconds}ms — playerState=${_audioService.streamPlayerState}');
      // playStreamUrl returns once playback has been requested; that is our
      // earliest sign that the audio_stream is reachable.
      if (currentMode != AppMode.state2Uploading &&
          currentMode != AppMode.state3Responding) {
        _dlog('STREAM', 'audio prepared but mode is ${currentMode.name} — stopping');
        // user interrupted while we were starting playback
        await _audioService.stopStreamPlayer();
        return;
      }
      _audioPlaybackActive = true;
      goState3('audio');
    } catch (e, st) {
      _dlog('STREAM',
          'audio_stream play() THREW after ${DateTime.now().difference(tAudio).inMilliseconds}ms (seq=$myAudioSeq cur=$_audioStreamSeq): $e');
      _dlog('STREAM', 'audio_stream stack: $st');
      // If the seq has moved on, this exception belongs to a stale turn —
      // probably triggered by the new turn's stop()/release() tearing the
      // pending MediaPlayer down. Swallow it silently.
      if (myAudioSeq != _audioStreamSeq) return;
      if (!transitioned && currentMode == AppMode.state2Uploading) {
        _errorResetToKws('流式连接失败，请检查网络');
      }
    }
  }

  // ────────────────────────────────────────────────────────────────────────────
  // state 3 — cloud is streaming; loop here until done or interrupted
  // ────────────────────────────────────────────────────────────────────────────

  void _enterState3Responding() {
    // Do NOT reset KWS here — we want to keep the rolling feature stream that
    // started in state2 so an interrupt word said around the state2→state3
    // boundary is still detected. (Reset already happened on state2 entry.)
    _setState(AppMode.state3Responding,
        _accumulatedLlmReply.isEmpty ? 'Aura 正在回答...' : 'Aura: $_accumulatedLlmReply');
    // Note: we do NOT subscribe to onStreamComplete here. audioplayers fires
    // that event prematurely for streamed UrlSource (the HTTP body finishes
    // well before buffered audio drains). Real end-of-playback is detected
    // via _waitForCloudAudioDrain, kicked off when the text stream closes.
  }

  // Shared interrupt-keyword handler for state2 (uploading / cloud thinking)
  // and state3 (cloud streaming). Listens for "请等一下".
  void _handleInterruptKws(Float32List frame) {
    final keyword = _kwsService.detectKeyword(frame);
    if (keyword.isEmpty) return;
    _dlog('KWS', 'detected="$keyword" in ${currentMode.name}');
    if (keyword.contains(AppConfig.interruptKeyword)) {
      _kwsService.resetStream();
      _enterState4Interrupting();
    }
  }

  void _onCloudStreamFinished() {
    _cloudStreamActive = false;
    // text done is the cue to start watching for the audio buffer to drain
    if (currentMode == AppMode.state3Responding) {
      unawaited(_waitForCloudAudioDrain());
    }
  }

  // Poll the cloud stream player until it has been in a non-active state
  // (completed/stopped) for a stable window. This is the workaround for
  // audioplayers firing onPlayerComplete before the buffered audio is done.
  Future<void> _waitForCloudAudioDrain() async {
    final myGen = _stateGeneration;
    final start = DateTime.now();
    const pollInterval = Duration(milliseconds: 100);
    const stableWindow = Duration(milliseconds: 500);
    const startupGrace = Duration(seconds: 3); // if never observed playing
    const maxWait = Duration(seconds: 60);

    bool observedActive = false;
    DateTime? doneSince;

    while (true) {
      if (_isDisposed || myGen != _stateGeneration) return;

      if (_audioService.isStreamPlayerActive) {
        observedActive = true;
        doneSince = null;
      } else if (observedActive) {
        doneSince ??= DateTime.now();
        if (DateTime.now().difference(doneSince) >= stableWindow) break;
      } else if (DateTime.now().difference(start) > startupGrace) {
        // never saw playback start; assume the audio finished or never began
        break;
      }

      if (DateTime.now().difference(start) > maxWait) {
        debugPrint('Cloud audio drain timeout in state3');
        break;
      }
      await Future.delayed(pollInterval);
    }

    if (_isDisposed || myGen != _stateGeneration) return;
    _audioPlaybackActive = false;
    _maybeAdvanceFromState3();
  }

  void _maybeAdvanceFromState3() {
    if (_isDisposed) return;
    if (currentMode != AppMode.state3Responding) return;
    // wait until BOTH the text stream is closed AND the audio playback ended
    if (_cloudStreamActive || _audioPlaybackActive) return;
    _enterState5Followup();
  }

  // ────────────────────────────────────────────────────────────────────────────
  // state 4 — send interrupt to cloud, then back to state 1
  // ────────────────────────────────────────────────────────────────────────────

  Future<void> _enterState4Interrupting() async {
    _dlog('STATE4', 'enter sid=$_currentSessionId');
    _setState(AppMode.state4Interrupting, '好的，我先停下...');

    _textStreamSubscription?.cancel();
    _textStreamSubscription = null;
    _cloudStreamActive = false;
    _audioPlaybackActive = false;
    // Invalidate any inflight audio_stream prepare so its late completion
    // (we've seen 22s+ on slow cloud turns) cannot reach the success path
    // and start playing the answer the user just told us to abort.
    _audioStreamSeq++;
    await _audioService.stopAllPlayers();

    final sid = _currentSessionId;
    if (sid != null) {
      _dlog('STATE4', 'POST /interrupt/$sid');
      final ok = await _apiService.interrupt(sid);
      _dlog('STATE4', '/interrupt returned ok=$ok  sid still=$_currentSessionId');
    } else {
      _dlog('STATE4', 'no sid cached, skipping /interrupt');
    }
    if (_isDisposed) return;
    _enterState1Listening();
  }

  // ────────────────────────────────────────────────────────────────────────────
  // state 5 — play am_i_clear.wav, listen for ack / new question / timeout
  // ────────────────────────────────────────────────────────────────────────────

  Future<void> _enterState5Followup() async {
    _cancelFollowupTimer();
    _followupListening = false;
    _pcmBuffer.clear();
    _setState(AppMode.state5Followup, '我说清楚了吗？');

    try {
      await _audioService.playAssetAndWait('wav/am_i_clear.wav');
    } catch (e) {
      debugPrint('Failed to play am_i_clear.wav: $e');
    }
    if (_isDisposed || currentMode != AppMode.state5Followup) return;

    _kwsService.hardResetStream();
    _followupListening = true;
    _lastLoudTime = DateTime.now();
    _followupTimer = Timer(
      const Duration(milliseconds: AppConfig.followupNoReplyMs),
      _onFollowupTimeout,
    );
  }

  void _handleState5(List<int> data, Float32List frame, double currentVolume) {
    if (!_followupListening) return;

    // 1) keyword check first — "我明白了" jumps straight to state 6
    final keyword = _kwsService.detectKeyword(frame);
    if (keyword.contains(AppConfig.acknowledgeKeyword)) {
      _cancelFollowupTimer();
      _followupListening = false;
      _enterState6SessionEnd();
      return;
    }

    // 2) any speech cancels the no-reply timer and starts a follow-up recording
    if (currentVolume > AppConfig.volumeThreshold) {
      _lastLoudTime = DateTime.now();
      if (_followupTimer != null) {
        _cancelFollowupTimer();
        _pcmBuffer.clear();
      }
      final safeLength = data.length % 2 == 0 ? data.length : data.length - 1;
      _pcmBuffer.addAll(data.sublist(0, safeLength));
      return;
    }

    // 3) if we are mid-recording, append silence and check for VAD end
    if (_followupTimer == null) {
      final safeLength = data.length % 2 == 0 ? data.length : data.length - 1;
      _pcmBuffer.addAll(data.sublist(0, safeLength));

      final quietMs = DateTime.now().difference(_lastLoudTime!).inMilliseconds;
      final recordSec = _pcmBuffer.length / (AppConfig.sampleRate * 2);
      if (quietMs > AppConfig.quietDurationMs ||
          recordSec > AppConfig.maxRecordSeconds) {
        _followupListening = false;
        _enterState2Uploading();
      }
    }
  }

  void _onFollowupTimeout() {
    _followupTimer = null;
    if (_isDisposed) return;
    if (currentMode != AppMode.state5Followup) return;
    _followupListening = false;
    _enterState6SessionEnd();
  }

  void _cancelFollowupTimer() {
    _followupTimer?.cancel();
    _followupTimer = null;
  }

  // ────────────────────────────────────────────────────────────────────────────
  // state 6 — tell cloud we're done, clear local state, back to state 0
  // ────────────────────────────────────────────────────────────────────────────

  Future<void> _enterState6SessionEnd() async {
    _setState(AppMode.state6SessionEnd, '本轮对话已结束');
    _textStreamSubscription?.cancel();
    _textStreamSubscription = null;
    _cloudStreamActive = false;
    _audioPlaybackActive = false;
    // Stop only the cloud stream; the prompt player must stay free so that
    // bye.wav (also a prompt) can play below without being preempted.
    await _audioService.stopStreamPlayer();

    // Play the goodbye prompt sequentially, exactly like the other local
    // prompts (i_am_listening, am_i_clear). Awaiting full completion is what
    // guarantees it actually emits — see the comment in _enterState2Uploading
    // for why we abandoned fire-and-forget plays on Android.
    try {
      await _audioService.playAssetAndWait('wav/bye.wav');
    } catch (e) {
      debugPrint('Failed to play bye.wav: $e');
    }
    if (_isDisposed) return;

    // Tell the cloud the multi-turn dialogue is done so it can free context.
    final sid = _currentSessionId;
    await _apiService.sessionComplete(sessionId: sid);
    if (_isDisposed) return;
    _enterState0Kws();
  }

  // ────────────────────────────────────────────────────────────────────────────
  // error path
  // ────────────────────────────────────────────────────────────────────────────

  void _errorResetToKws(String errorMsg) {
    _textStreamSubscription?.cancel();
    _textStreamSubscription = null;
    _cancelFollowupTimer();
    _followupListening = false;
    _vadArmed = false;
    _cloudStreamActive = false;
    _audioPlaybackActive = false;

    _setState(AppMode.state0Kws, errorMsg);

    Future.delayed(const Duration(seconds: 2), () {
      if (_isDisposed) return;
      _enterState0Kws();
    });
  }

  @override
  void dispose() {
    _isDisposed = true;
    _cancelFollowupTimer();
    _micSubscription?.cancel();
    _textStreamSubscription?.cancel();
    _audioService.dispose();
    _kwsService.dispose();
    super.dispose();
  }
}
