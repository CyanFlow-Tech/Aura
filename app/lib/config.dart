import 'package:flutter_dotenv/flutter_dotenv.dart';

class AppConfig {
  // network configuration
  static String get serverIp => dotenv.env['AURA_SERVER_IP'] ?? '127.0.0.1';
  static int get serverPort => int.parse(dotenv.env['AURA_SERVER_PORT'] ?? '18000');
  static String get apiKey => dotenv.env['AURA_API_KEY'] ?? 'your_secret_key_here';
  static String get baseUrl => 'https://$serverIp:$serverPort/api/aura';

  // audio hard metrics
  static const int sampleRate = 16000;
  
  // business logic thresholds
  static const double volumeThreshold = 0.02; // trigger volume threshold
  static const int quietDurationMs = 3000;    // quiet duration threshold (milliseconds)
  static const double maxRecordSeconds = 20.0;// maximum recording duration (seconds)

  // multi-turn keywords (must also exist in assets/kws_model/keywords.txt)
  static const String wakeKeyword = '小爱同学';
  static const String interruptKeyword = '请等一下';
  static const String acknowledgeKeyword = '我明白了';

  // followup (state 5) timing
  static const int followupNoReplyMs = 4000;  // no-reply timeout after am_i_clear.wav
}