import 'package:flutter/material.dart';

class UsageBar extends StatelessWidget {
  final Map<String, dynamic> usage;
  final String tier;

  const UsageBar({super.key, required this.usage, required this.tier});

  @override
  Widget build(BuildContext context) {
    final dailyUsed = (usage['daily_used'] ?? usage['used'] ?? 0) as int;
    final dailyLimit = (usage['daily_limit'] ?? usage['limit'] ?? 1) as int;
    final monthlyUsed = (usage['monthly_used'] ?? 0) as int;
    final monthlyLimit = usage['monthly_limit'] as int?;
    final adCredits = (usage['ad_credits'] ?? 0) as int;
    final pct = dailyLimit > 0 ? (dailyUsed / dailyLimit).clamp(0.0, 1.0) : 0.0;

    return Container(
      color: const Color(0xFFEFF6FF),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    value: pct,
                    backgroundColor: const Color(0xFFBFDBFE),
                    color: const Color(0xFF3B82F6),
                    minHeight: 6,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Text(
                '$dailyUsed/$dailyLimit${adCredits > 0 ? ' (+$adCredits)' : ''}',
                style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600,
                    color: Color(0xFF1D4ED8)),
              ),
            ],
          ),
          if (monthlyLimit != null)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(
                '이번 달 $monthlyUsed/$monthlyLimit문항',
                style: const TextStyle(fontSize: 11, color: Color(0xFF3B82F6)),
              ),
            ),
        ],
      ),
    );
  }
}
