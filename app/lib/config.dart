class AppConfig {
  // network configuration
  static const String serverIp = '192.168.1.114';
  static const int serverPort = 18000;
  static String get baseUrl => 'http://$serverIp:$serverPort/api/aura';

  // audio hard metrics
  static const int sampleRate = 16000;
  
  // business logic thresholds
  static const double volumeThreshold = 0.02; // trigger volume threshold
  static const int quietDurationMs = 3000;    // quiet duration threshold (milliseconds)
  static const double maxRecordSeconds = 20.0;// maximum recording duration (seconds)
}