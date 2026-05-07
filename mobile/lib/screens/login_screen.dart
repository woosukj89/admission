import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

const _apiBase = String.fromEnvironment('API_BASE', defaultValue: 'https://your-api.example.com');

class LoginScreen extends StatefulWidget {
  final void Function(String token) onLogin;
  const LoginScreen({super.key, required this.onLogin});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  bool _loading = false;
  String? _error;

  final _googleSignIn = GoogleSignIn(scopes: ['email', 'profile', 'openid']);

  Future<void> _signInWithGoogle() async {
    setState(() { _loading = true; _error = null; });
    try {
      final account = await _googleSignIn.signIn();
      if (account == null) {
        setState(() => _loading = false);
        return;
      }
      final auth = await account.authentication;
      final idToken = auth.idToken;
      if (idToken == null) throw Exception('ID token missing');

      final resp = await http.post(
        Uri.parse('$_apiBase/api/auth/mobile'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'id_token': idToken}),
      );

      if (resp.statusCode != 200) {
        throw Exception('서버 오류: ${resp.body}');
      }

      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final token = data['token'] as String;

      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('jwt_token', token);

      widget.onLogin(token);
    } catch (e) {
      setState(() { _error = e.toString(); _loading = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text('🎓', style: TextStyle(fontSize: 64)),
                const SizedBox(height: 16),
                const Text('입시 AI 상담사',
                    style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                const Text('내신·수능 성적으로 맞춤 대학을 추천받으세요.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.grey, fontSize: 14)),
                const SizedBox(height: 40),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: Text(_error!, style: const TextStyle(color: Colors.red, fontSize: 13)),
                  ),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    onPressed: _loading ? null : _signInWithGoogle,
                    icon: _loading
                        ? const SizedBox(width: 20, height: 20,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.login),
                    label: const Text('Google로 로그인'),
                    style: OutlinedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 14),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
