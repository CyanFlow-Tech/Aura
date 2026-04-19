import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'controller.dart';
import 'dart:io';
import 'config.dart';

class LabHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) {
    return super.createHttpClient(context)
      ..badCertificateCallback = (X509Certificate cert, String host, int port) {
        return host == AppConfig.serverIp;
      };
  }
}

Future<void> main() async {
  await dotenv.load(fileName: ".env");
  HttpOverrides.global = LabHttpOverrides(); 
  runApp(const AuraApp());
}


class AuraApp extends StatelessWidget {
  const AuraApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Aura',
      theme: ThemeData(
        brightness: Brightness.dark, 
        scaffoldBackgroundColor: Colors.black
      ),
      home: const AuraHomePage(),
    );
  }
}

class AuraHomePage extends StatefulWidget {
  const AuraHomePage({super.key});

  @override
  State<AuraHomePage> createState() => _AuraHomePageState();
}

class _AuraHomePageState extends State<AuraHomePage> {
  late final AuraController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AuraController();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: ListenableBuilder(
          listenable: _controller,
          builder: (context, child) {
            final mode = _controller.currentMode;
            return Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(
                  _iconForMode(mode),
                  size: 80,
                  color: _colorForMode(mode),
                ),
                const SizedBox(height: 30),
                Text(
                  _controller.displayText,
                  textAlign: TextAlign.justify,
                  style: const TextStyle(
                    fontSize: 20, // Slightly smaller for long text
                    fontWeight: FontWeight.w500,
                    height: 1.4,
                  ),
                ),
              ],
            );
          },
        ),
      ),
    );
  }

  IconData _iconForMode(AppMode mode) {
    switch (mode) {
      case AppMode.state0Kws:
        return Icons.hearing;
      case AppMode.state1Listening:
        return Icons.mic;
      case AppMode.state2Uploading:
        return Icons.cloud_upload;
      case AppMode.state3Responding:
        return Icons.record_voice_over;
      case AppMode.state4Interrupting:
        return Icons.pan_tool;
      case AppMode.state5Followup:
        return Icons.help_outline;
      case AppMode.state6SessionEnd:
        return Icons.check_circle_outline;
    }
  }

  Color _colorForMode(AppMode mode) {
    switch (mode) {
      case AppMode.state0Kws:
        return Colors.cyanAccent;
      case AppMode.state1Listening:
        return Colors.redAccent;
      case AppMode.state2Uploading:
        return Colors.amberAccent;
      case AppMode.state3Responding:
        return Colors.greenAccent;
      case AppMode.state4Interrupting:
        return Colors.orangeAccent;
      case AppMode.state5Followup:
        return Colors.lightBlueAccent;
      case AppMode.state6SessionEnd:
        return Colors.grey;
    }
  }
}