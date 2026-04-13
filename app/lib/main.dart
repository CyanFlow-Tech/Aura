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
            return Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(
                  _controller.currentMode == AppMode.recording ? Icons.mic 
                    : _controller.currentMode == AppMode.kws ? Icons.hearing 
                    : Icons.api,
                  size: 80,
                  color: _controller.currentMode == AppMode.recording ? Colors.redAccent 
                    : _controller.currentMode == AppMode.kws ? Colors.cyanAccent 
                    : Colors.grey,
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
}