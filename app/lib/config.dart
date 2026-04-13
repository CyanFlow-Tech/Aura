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
}