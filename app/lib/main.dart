import 'package:flutter/material.dart';
import 'controller.dart';

void main() {
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
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    fontSize: 24, 
                    fontWeight: FontWeight.bold, 
                    height: 1.5
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