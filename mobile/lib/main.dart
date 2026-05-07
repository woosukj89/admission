import 'package:flutter/material.dart';
import 'package:google_mobile_ads/google_mobile_ads.dart';
import 'package:purchases_flutter/purchases_flutter.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'screens/login_screen.dart';
import 'screens/chat_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await MobileAds.instance.initialize();
  await _initRevenueCat();
  runApp(const AdmissionApp());
}

Future<void> _initRevenueCat() async {
  const rcKey = String.fromEnvironment('REVENUECAT_KEY', defaultValue: '');
  if (rcKey.isEmpty) return;
  await Purchases.setLogLevel(LogLevel.debug);
  final config = PurchasesConfiguration(rcKey);
  await Purchases.configure(config);
}

class AdmissionApp extends StatelessWidget {
  const AdmissionApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '입시 AI 상담',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF3B82F6)),
        useMaterial3: true,
        fontFamily: 'NotoSansKR',
      ),
      home: const AuthGate(),
    );
  }
}

class AuthGate extends StatefulWidget {
  const AuthGate({super.key});

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  bool _checking = true;
  String? _token;

  @override
  void initState() {
    super.initState();
    _checkAuth();
  }

  Future<void> _checkAuth() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('jwt_token');
    setState(() {
      _token = token;
      _checking = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_checking) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    if (_token != null) {
      return ChatScreen(token: _token!);
    }
    return LoginScreen(onLogin: (token) {
      setState(() => _token = token);
    });
  }
}
