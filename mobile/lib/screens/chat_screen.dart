import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:google_mobile_ads/google_mobile_ads.dart';
import 'package:http/http.dart' as http;
import 'package:purchases_flutter/purchases_flutter.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../services/api_service.dart';
import '../services/ad_service.dart';
import '../widgets/usage_bar.dart';

class ChatScreen extends StatefulWidget {
  final String token;
  const ChatScreen({super.key, required this.token});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();
  final List<Map<String, String>> _messages = [];
  final List<Map<String, dynamic>> _history = [];
  bool _loading = false;
  Map<String, dynamic>? _usage;
  String _tier = 'free';
  bool _paymentsEnabled = false;

  late final ApiService _api;

  @override
  void initState() {
    super.initState();
    _api = ApiService(token: widget.token);
    _fetchUsage();
    _fetchMe();
    _fetchConfig();
  }

  Future<void> _fetchConfig() async {
    try {
      final data = await _api.get('/api/config');
      setState(() => _paymentsEnabled = data['payments_enabled'] == true);
    } catch (_) {}
  }

  Future<void> _fetchMe() async {
    try {
      final data = await _api.get('/api/me');
      setState(() => _tier = data['tier'] ?? 'free');
    } catch (_) {}
  }

  Future<void> _fetchUsage() async {
    try {
      final data = await _api.get('/api/usage');
      setState(() => _usage = data);
    } catch (_) {}
  }

  Future<void> _sendMessage() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _loading) return;
    _controller.clear();
    setState(() {
      _messages.add({'role': 'user', 'content': text});
      _loading = true;
    });
    _scrollToBottom();

    // Send to API via SSE streaming
    final botIndex = _messages.length;
    setState(() => _messages.add({'role': 'model', 'content': ''}));

    try {
      final buffer = StringBuffer();
      await for (final chunk in _api.streamChat(text, _history)) {
        if (chunk.containsKey('status')) continue;
        final part = chunk['text'] as String? ?? '';
        buffer.write(part);
        if (mounted) {
          setState(() => _messages[botIndex] = {'role': 'model', 'content': buffer.toString()});
          _scrollToBottom();
        }
      }
      _history.add({'role': 'user', 'parts': [text]});
      _history.add({'role': 'model', 'parts': [buffer.toString()]});
    } on RateLimitException {
      if (_tier == 'free') {
        _showAdOrUpgrade();
      }
      if (mounted) {
        setState(() => _messages[botIndex] = {
          'role': 'model', 'content': '오늘 질문 한도를 초과했습니다.',
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => _messages[botIndex] = {'role': 'model', 'content': '오류: $e'});
      }
    }

    if (mounted) {
      setState(() => _loading = false);
      await _fetchUsage();
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _showAdOrUpgrade() {
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('오늘 무료 질문을 모두 사용했습니다.',
                  style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              const SizedBox(height: 8),
              const Text('광고를 시청하면 1문항을 더 받을 수 있어요.',
                  style: TextStyle(color: Colors.grey)),
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () async {
                    Navigator.pop(ctx);
                    await _watchAd();
                  },
                  child: const Text('광고 보고 1문항 받기'),
                ),
              ),
              if (_paymentsEnabled)
                const SizedBox(height: 8),
              if (_paymentsEnabled)
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton(
                    onPressed: () {
                      Navigator.pop(ctx);
                      _showUpgradeSheet();
                    },
                    child: const Text('프리미엄 구독 (월 8,000원)'),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _watchAd() async {
    final granted = await AdService.showRewardedAd();
    if (!granted) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('광고를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.')),
        );
      }
      return;
    }
    try {
      await _api.post('/api/credits/ad', {});
      await _fetchUsage();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('✅ 1문항 크레딧이 추가되었습니다!')),
        );
      }
    } catch (_) {}
  }

  Future<void> _showUpgradeSheet() async {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('🚀 프리미엄으로 업그레이드',
                  style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
              const SizedBox(height: 16),
              const _FeatureRow('하루 5문항 / 한 달 100문항'),
              const _FeatureRow('Claude AI (더 정확한 답변)'),
              const _FeatureRow('월 8,000원'),
              const SizedBox(height: 24),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () async {
                    Navigator.pop(ctx);
                    await _purchase();
                  },
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF3B82F6),
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                  ),
                  child: const Text('구독 시작', style: TextStyle(fontSize: 16)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _purchase() async {
    try {
      final offerings = await Purchases.getOfferings();
      final current = offerings.current;
      if (current == null) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('구독 상품을 불러올 수 없습니다.')),
          );
        }
        return;
      }
      final package = current.monthly ?? current.availablePackages.firstOrNull;
      if (package == null) return;
      await Purchases.purchasePackage(package);
      // RevenueCat webhook will update tier on server; refresh
      await _fetchMe();
      await _fetchUsage();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('✅ 프리미엄 구독이 완료되었습니다!')),
        );
      }
    } on PurchasesErrorCode catch (e) {
      if (e == PurchasesErrorCode.purchaseCancelledError) return;
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('구독 오류: $e')),
        );
      }
    }
  }

  Future<void> _logout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('jwt_token');
    if (mounted) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const _LogoutPlaceholder()),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('🎓 입시 AI 상담', style: TextStyle(fontSize: 16)),
        actions: [
          if (_tier == 'free')
            TextButton(
              onPressed: _showUpgradeSheet,
              child: const Text('업그레이드', style: TextStyle(color: Color(0xFF3B82F6))),
            ),
          PopupMenuButton<String>(
            onSelected: (v) { if (v == 'logout') _logout(); },
            itemBuilder: (_) => [
              const PopupMenuItem(value: 'logout', child: Text('로그아웃')),
            ],
          ),
        ],
      ),
      body: Column(
        children: [
          if (_usage != null)
            UsageBar(usage: _usage!, tier: _tier),
          Expanded(
            child: _messages.isEmpty
                ? _buildWelcome()
                : ListView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.all(12),
                    itemCount: _messages.length,
                    itemBuilder: (_, i) => _buildBubble(_messages[i]),
                  ),
          ),
          _buildInputRow(),
        ],
      ),
    );
  }

  Widget _buildWelcome() {
    return const Center(
      child: Padding(
        padding: EdgeInsets.all(32),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text('안녕하세요! 입시 AI 상담사입니다. 🎓',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
          SizedBox(height: 8),
          Text('내신이나 수능 성적을 알려주시면\n맞춤형 대학 추천을 도와드립니다.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey)),
        ]),
      ),
    );
  }

  Widget _buildBubble(Map<String, String> msg) {
    final isUser = msg['role'] == 'user';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.8),
        decoration: BoxDecoration(
          color: isUser ? const Color(0xFF3B82F6) : Colors.white,
          borderRadius: BorderRadius.circular(16),
          boxShadow: [BoxShadow(color: Colors.black12, blurRadius: 4, offset: const Offset(0, 2))],
        ),
        padding: const EdgeInsets.all(12),
        child: isUser
            ? Text(msg['content'] ?? '', style: const TextStyle(color: Colors.white))
            : MarkdownBody(
                data: msg['content']?.isEmpty == true ? '●  ●  ●' : msg['content']!,
                styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)),
              ),
      ),
    );
  }

  Widget _buildInputRow() {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
        child: Row(children: [
          Expanded(
            child: TextField(
              controller: _controller,
              enabled: !_loading,
              maxLines: null,
              decoration: InputDecoration(
                hintText: '질문을 입력하세요...',
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              ),
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => _sendMessage(),
            ),
          ),
          const SizedBox(width: 8),
          IconButton(
            onPressed: _loading ? null : _sendMessage,
            icon: _loading
                ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.send, color: Color(0xFF3B82F6)),
          ),
        ]),
      ),
    );
  }
}

class _FeatureRow extends StatelessWidget {
  final String text;
  const _FeatureRow(this.text);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(children: [
        const Icon(Icons.check_circle, color: Colors.green, size: 20),
        const SizedBox(width: 8),
        Text(text),
      ]),
    );
  }
}

class _LogoutPlaceholder extends StatelessWidget {
  const _LogoutPlaceholder();

  @override
  Widget build(BuildContext context) {
    // AuthGate will re-check and show login screen
    return const Scaffold(body: Center(child: CircularProgressIndicator()));
  }
}
