import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;

const _apiBase = String.fromEnvironment('API_BASE', defaultValue: 'https://your-api.example.com');

class RateLimitException implements Exception {}

class ApiService {
  final String token;
  final _client = http.Client();

  ApiService({required this.token});

  Map<String, String> get _headers => {
    'Authorization': 'Bearer $token',
    'Content-Type': 'application/json',
  };

  Future<Map<String, dynamic>> get(String path) async {
    final resp = await _client.get(Uri.parse('$_apiBase$path'), headers: _headers);
    if (resp.statusCode == 200) return jsonDecode(resp.body) as Map<String, dynamic>;
    throw Exception('GET $path → ${resp.statusCode}');
  }

  Future<Map<String, dynamic>> post(String path, Map<String, dynamic> body) async {
    final resp = await _client.post(
      Uri.parse('$_apiBase$path'),
      headers: _headers,
      body: jsonEncode(body),
    );
    if (resp.statusCode == 200) return jsonDecode(resp.body) as Map<String, dynamic>;
    if (resp.statusCode == 429) throw RateLimitException();
    throw Exception('POST $path → ${resp.statusCode}: ${resp.body}');
  }

  Stream<Map<String, dynamic>> streamChat(
    String message,
    List<Map<String, dynamic>> history,
  ) async* {
    final request = http.Request('POST', Uri.parse('$_apiBase/api/chat'));
    request.headers.addAll(_headers);
    request.body = jsonEncode({'message': message, 'history': history});

    final response = await _client.send(request);
    if (response.statusCode == 429) throw RateLimitException();
    if (response.statusCode != 200) {
      throw Exception('Chat → ${response.statusCode}');
    }

    var buffer = '';
    await for (final chunk in response.stream.transform(utf8.decoder)) {
      buffer += chunk;
      final lines = buffer.split('\n');
      buffer = lines.removeLast();

      for (final line in lines) {
        if (!line.startsWith('data: ')) continue;
        final data = line.substring(6);
        if (data == '[DONE]') return;
        try {
          final parsed = jsonDecode(data);
          if (parsed is Map && parsed.containsKey('status')) {
            yield {'status': parsed['status']};
          } else if (parsed is String) {
            yield {'text': parsed};
          }
        } catch (_) {
          yield {'text': data};
        }
      }
    }
  }
}
